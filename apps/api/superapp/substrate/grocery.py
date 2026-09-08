"""Grocery twin operations — the only module touching the grocery tables.

The shelf in the design is this file's `grocery_context`: items grouped by
category, plus the two shelves the forecast produces (Running low, Out of
stock). Everything here is deterministic; the model's only job in this vertical
is reading receipts, which happens in the agent.
"""
import re
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from ..grocery.predict import Bought, forecast
from ..grocery.units import parse_size
from ..models import (GmailAccount, GroceryItem, GroceryLink, GroceryOrder, GroceryPurchase,
                      utcnow)

# The shelves, in the order they are drawn. "Running low" and "Out of stock"
# are computed, not stored: they are a view of the same items.
CATEGORIES = ["Fresh Produce", "Grains", "Dairy & Protein", "Snacks",
              "Beverages", "Household Essentials"]
_CANON = {c.lower(): c for c in CATEGORIES}

# Packaging and marketing words. Stripped so the same product spelled two ways
# collapses to one shelf item; kept short, because over-stripping merges things
# that are genuinely different ("whole milk" vs "oat milk" must not collide).
# Packaging and merchandising words only. NOT variants: "whole", "oat", "1%",
# "organic", "decaf" and their kind distinguish genuinely different products and
# must survive, or two things a household buys separately become one shelf item
# whose purchase history is an average of neither.
_PACKAGING = re.compile(
    r"\b(pack|pk|pkg|ct|count|ea|each|dozen|doz|bag|box|btl|bottle|jar|can|"
    r"carton|tub|roll|rolls|oz|floz|lb|lbs|kg|g|gr|ml|l|lt|ltr|gal|gallon|"
    r"qt|quart|pt|pint|liter|litre|gram|grams|pound|pounds|ounce|ounces)\b", re.I)
_MERCHANDISING = re.compile(
    r"\b(great|value|brand|family|size|club|select|signature|essentials?)\b", re.I)
# A number glued to or followed by a unit is a package size, not a name.
# Handled before bare digits so "1gal" and "1 gal" both disappear.
#
# `mg` is deliberately absent from both lists: milligrams are a STRENGTH, not a
# package size. Nobody buys 500mg of flour, and "Advil 200mg" and "Advil 500mg"
# are different products whose purchase histories must not be interleaved.
_SIZE_EXPR = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:x\s*\d+(?:\.\d+)?\s*)?"
    r"(?:oz|floz|fl\s*oz|lb|lbs|kg|g|gr|ml|l|lt|ltr|gal|gallon|qt|quart|pt|"
    r"pint|ct|count|pk|pack|dozen|doz|liter|litre|gram|grams|pound|pounds|"
    r"ounce|ounces)\b", re.I)


def slugify(name: str) -> str:
    """Two receipts spell the same thing differently ("Milk, Whole 1 Gal" and
    "WHOLE MILK 1GAL"). Both must land on ONE shelf item, or the forecast sees
    two items bought once each and never learns a rate.

    But only the same THING. Package size is stripped, because a gallon and a
    half-gallon of the same milk are one product bought in two amounts and the
    forecast handles that in `units.py`. Variants are kept: "Milk 1%" and
    "Milk 2%" are different products, and merging their purchases makes both
    predictions wrong.
    """
    s = (name or "").lower()
    # Percentages carry meaning ("2% milk") and would otherwise be destroyed by
    # punctuation and digit stripping, taking the distinction with them.
    s = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"\1pct", s)
    s = _SIZE_EXPR.sub(" ", s)
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = _PACKAGING.sub(" ", s)
    s = _MERCHANDISING.sub(" ", s)
    # Numbers that survived the size pass are part of the NAME: "Vitamin D3
    # 5000 IU", "7 Up", "Advil 200". Stripping them merged different dosages
    # into one shelf item with interleaved history — the same corruption as
    # merging 1% and 2% milk. Only long digit runs are dropped, because those
    # are receipt lot and SKU codes rather than anything a person would say.
    s = re.sub(r"\b\d{6,}\b", " ", s)
    tokens = sorted(set(t for t in s.split() if t))
    return " ".join(tokens)[:120] or re.sub(r"\s+", " ", (name or "").lower()).strip()[:120]


def canonical_category(raw: str) -> str:
    c = (raw or "").strip().lower()
    if c in _CANON:
        return _CANON[c]
    for key, label in _CANON.items():
        if c and (c in key or key.split(" &")[0] in c):
            return label
    return "Household Essentials" if c else ""


def upsert_item(db: Session, *, user_id: str, name: str, category: str = "",
                brand: str = "", size: str = "", unit: str = "",
                image_ref: str = "") -> GroceryItem:
    slug = slugify(name)
    item = db.scalar(select(GroceryItem).where(
        GroceryItem.user_id == user_id, GroceryItem.slug == slug))
    if item is None:
        item = GroceryItem(user_id=user_id, slug=slug, name=name[:120],
                           category=canonical_category(category))
        db.add(item)
        db.flush()
    # Fill blanks, never overwrite: a receipt line should not rename an item
    # the person named themselves.
    for field, value in (("brand", brand), ("size", size), ("unit", unit),
                         ("image_ref", image_ref)):
        if value and not getattr(item, field):
            setattr(item, field, value[:200])
    if category and not item.category:
        item.category = canonical_category(category)
    item.updated_at = utcnow()
    return item


def record_purchase(db: Session, *, user_id: str, item: GroceryItem,
                    purchased_at: datetime, quantity: float = 1.0,
                    source: str = "manual", source_ref: str = "",
                    merchant: str = "", unit_price_cents: int | None = None,
                    size_text: str = "") -> GroceryPurchase | None:
    """Idempotent per (source, source_ref, item): re-reading a receipt is not
    a second shopping trip. Returns None when it was already known.

    `size_text` is the package size as the receipt wrote it ("1 gal", "500g");
    it is normalised here so the forecast can compare purchases of different
    sizes. Import order does not matter: a receipt older than the person's own
    "I'm out" never revives the item — only shopping done SINCE the correction
    does. Backfilling last quarter's receipts must not tell someone they have
    milk they threw away this morning.
    """
    existing = db.scalar(select(GroceryPurchase).where(
        GroceryPurchase.user_id == user_id, GroceryPurchase.item_id == item.id,
        GroceryPurchase.source == source, GroceryPurchase.source_ref == (source_ref or "")))
    if existing is not None:
        return None
    when = purchased_at if purchased_at.tzinfo else purchased_at.replace(tzinfo=timezone.utc)
    parsed = parse_size(size_text or item.size or "")
    row = GroceryPurchase(user_id=user_id, item_id=item.id, source=source,
                          source_ref=(source_ref or "")[:512], merchant=merchant[:80],
                          quantity=float(quantity or 1), unit_price_cents=unit_price_cents,
                          pack_amount=(parsed[0] if parsed else None),
                          pack_unit=(parsed[1] if parsed else ""),
                          purchased_at=when)
    db.add(row)
    last = item.last_purchased_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    if last is None or when > last:
        item.last_purchased_at = when
    # Buying it settles the question — but only if the buying came AFTER the
    # person said they were out. An imported old receipt is news about the
    # past, not evidence about the cupboard right now.
    declared = item.declared_out_at
    if declared is not None and declared.tzinfo is None:
        declared = declared.replace(tzinfo=timezone.utc)
    if declared is None or when > declared:
        item.declared_out_at = None
        item.on_list = False
    item.updated_at = utcnow()
    db.flush()
    return row


def purchases_for(db: Session, user_id: str, item_id: str) -> list[Bought]:
    rows = db.scalars(select(GroceryPurchase).where(
        GroceryPurchase.user_id == user_id, GroceryPurchase.item_id == item_id)
        .order_by(GroceryPurchase.purchased_at)).all()
    return [Bought(at=p.purchased_at, quantity=p.quantity,
                   pack_amount=p.pack_amount, pack_unit=p.pack_unit or "") for p in rows]


def item_state(db: Session, user_id: str, item: GroceryItem, now=None,
               purchases: list | None = None) -> dict:
    """`purchases` lets a caller that already loaded them avoid a query per
    item — the shelf renders every item on every screen load."""
    f = forecast(purchases=(purchases if purchases is not None
                            else purchases_for(db, user_id, item.id)),
                 category=item.category, last_purchased_at=item.last_purchased_at,
                 declared_out_at=item.declared_out_at, now=now)
    return {
        "id": item.id, "name": item.name, "category": item.category or "Household Essentials",
        "brand": item.brand, "size": item.size, "unit": item.unit,
        "image_ref": item.image_ref, "on_list": item.on_list, "pinned": item.pinned,
        "last_purchased_at": (item.last_purchased_at.isoformat()
                              if item.last_purchased_at else ""),
        **f.as_dict(),
    }


def grocery_context(db: Session, user_id: str) -> dict:
    """The shelf slice of ContextSlice.domain_data — exactly what the screen draws."""
    items = list(db.scalars(select(GroceryItem).where(GroceryItem.user_id == user_id,
                            or_(GroceryItem.last_purchased_at.is_(None),
                                GroceryItem.last_purchased_at >= utcnow() - timedelta(days=120),
                                GroceryItem.pinned.is_(True), GroceryItem.on_list.is_(True),
                                GroceryItem.declared_out_at.isnot(None)))
                            .order_by(GroceryItem.name)))
    # One query for every purchase this person has, grouped in memory. Asking
    # per item cost 63 queries for a 60-item shelf, on every render.
    by_item: dict[str, list[Bought]] = {}
    for p in db.scalars(select(GroceryPurchase)
                        .where(GroceryPurchase.user_id == user_id)
                        .order_by(GroceryPurchase.purchased_at)):
        by_item.setdefault(p.item_id, []).append(
            Bought(at=p.purchased_at, quantity=p.quantity,
                   pack_amount=p.pack_amount, pack_unit=p.pack_unit or ""))
    states = [item_state(db, user_id, i, purchases=by_item.get(i.id, [])) for i in items]

    shelves = []
    for cat in CATEGORIES:
        on_shelf = [s for s in states if s["category"] == cat]
        if on_shelf:
            shelves.append({"category": cat, "items": on_shelf})

    low = sorted([s for s in states if s["status"] == "running_low"],
                 key=lambda s: s["days_left"])
    out = sorted([s for s in states if s["status"] == "out"],
                 key=lambda s: s["days_left"])

    orders = list(db.scalars(select(GroceryOrder).where(
        GroceryOrder.user_id == user_id, GroceryOrder.status.in_(("draft", "confirmed", "handed_off")))
        .order_by(GroceryOrder.created_at.desc()).limit(5)))
    linked = list(db.scalars(select(GroceryLink).where(
        GroceryLink.user_id == user_id, GroceryLink.status == "linked")))

    return {
        "mail_connected": bool(db.scalar(select(GmailAccount.id).where(GmailAccount.user_id == user_id).limit(1))),
        "shelves": shelves,
        "running_low": low,
        "out_of_stock": out,
        "list": [s for s in states if s["on_list"]],
        "item_count": len(states),
        # How much of this shelf is built on receipts rather than category
        # guesses. A screen full of "assumed" is a screen that has not earned
        # its predictions yet, and should say so instead of looking confident.
        "measured_count": sum(1 for s in states if s["basis"] in ("measured", "estimated")),
        "platforms": [{"platform": l.platform, "label": l.account_label,
                       "status": l.status} for l in linked],
        "pending_orders": [{"id": o.id, "platform": o.platform, "status": o.status,
                            "lines": o.lines or [], "reason": o.reason,
                            "subtotal_cents": o.subtotal_cents} for o in orders],
    }


def set_declared_out(db: Session, *, user_id: str, item_id: str, out: bool) -> GroceryItem:
    item = db.get(GroceryItem, item_id)
    if item is None or item.user_id != user_id:
        raise ValueError("No such item")
    item.declared_out_at = utcnow() if out else None
    if out:
        item.on_list = True
    item.updated_at = utcnow()
    return item


def add_to_basket(db: Session, *, user_id: str, items: list[GroceryItem]) -> GroceryOrder:
    order = db.scalar(select(GroceryOrder).where(GroceryOrder.user_id == user_id,
                      GroceryOrder.status.in_(("draft", "confirmed", "handed_off")))
                      .order_by(GroceryOrder.created_at.desc()).with_for_update())
    if order is None:
        order = GroceryOrder(user_id=user_id, platform="list", lines=[])
        db.add(order)
    lines = list(order.lines or [])
    known = {line["item_id"] for line in lines}
    for item in items:
        item.on_list = True
        if item.id not in known:
            lines.append({"item_id": item.id, "name": item.name, "quantity": 1, "unit": item.unit, "note": "You added this"})
            known.add(item.id)
    order.lines = lines
    order.reason = "Your shopping list"
    order.status, order.external_id = "draft", ""
    order.confirmed_by, order.confirmed_at = "", None
    db.flush()
    return order
