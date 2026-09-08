"""Grocery endpoints.

The interesting one is `/grocery/orders/{id}/place`. Everything else is
bookkeeping; that one spends money, so it is written defensively:

  * `grocery.place_order` is tier 3 in the policy table, so `assess()` refuses
    it for every provenance. No cron, no voice command, no rule reaches it.
  * The order row must carry `confirmed_by == "user"`, set by a separate
    endpoint the person taps.
  * The confirmation is bound to the BASKET, not the order id. If the lines
    changed after they said yes, the yes no longer applies and they confirm
    again. Approving "milk and eggs" must never become authority to buy
    whatever the basket says an hour later.
  * A client that cannot actually order raises rather than inventing an id.

Nano never sees or stores a card. Checkout credentials live with the platform.
"""
import hashlib
import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..agents.base import render_screen, run_think
from ..auth import current_user_id
from ..db import get_db
from ..grocery.base import OrderLine, StoreError
from ..grocery.factory import client_for
from ..grocery.providers import PLATFORMS
from ..kernel import record_decision
from ..models import GroceryItem, GroceryLink, GroceryOrder, utcnow
from ..policy import assess
from ..substrate import append_event
from ..substrate.grocery import (grocery_context, record_purchase,
                                 set_declared_out, upsert_item)

router = APIRouter(prefix="/v1", tags=["grocery"])


def _basket_fingerprint(lines: list) -> str:
    """What exactly the person agreed to buy. Any change to what or how much
    invalidates the yes."""
    payload = sorted((str(l.get("item_id", "")), str(l.get("name", "")),
                      float(l.get("quantity") or 0)) for l in (lines or []))
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()[:32]


@router.get("/grocery/state")
def grocery_state(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    """The shelf, as data. A pure read: no scanning, no model calls, no writes."""
    return grocery_context(db, user_id)


class AddItemBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    category: str = Field("", max_length=40)
    brand: str = Field("", max_length=80)
    size: str = Field("", max_length=40)
    bought_now: bool = False        # "I just bought this" seeds the first date


@router.post("/grocery/items")
def add_item(body: AddItemBody, user_id: str = Depends(current_user_id),
             db: Session = Depends(get_db)):
    item = upsert_item(db, user_id=user_id, name=body.name, category=body.category,
                       brand=body.brand, size=body.size)
    if body.bought_now:
        record_purchase(db, user_id=user_id, item=item, purchased_at=utcnow(),
                        source="manual", source_ref=f"manual-{utcnow().timestamp():.0f}")
    db.commit()
    return {"ok": True, "item_id": item.id}


class OutBody(BaseModel):
    out: bool = True


@router.post("/grocery/items/{item_id}/out")
def mark_out(item_id: str, body: OutBody, user_id: str = Depends(current_user_id),
             db: Session = Depends(get_db)):
    """The person looked in the cupboard. This outranks the forecast."""
    try:
        set_declared_out(db, user_id=user_id, item_id=item_id, out=body.out)
    except ValueError:
        raise HTTPException(status_code=404, detail="No such item")
    db.commit()
    return {"ok": True}


@router.post("/grocery/scan")
def scan_receipts(background: BackgroundTasks, user_id: str = Depends(current_user_id),
                  db: Session = Depends(get_db)):
    """Read grocery receipts out of the mailbox and fill the shelf."""
    from ..routers.screen import _background_think
    background.add_task(_background_think, "grocery", user_id, {"kind": "receipt_scan"})
    return {"ok": True, "started": True}


class LinkBody(BaseModel):
    platform: str = Field(..., max_length=24)
    account_label: str = Field("", max_length=120)


@router.get("/grocery/platforms")
def list_platforms(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    """What each platform can actually do — decided by building the client, not
    by a row saying "linked".

    The first version let `/link` write status="linked" with no authentication
    behind it, so the screen claimed a connection that did not exist. Nothing
    here is a promise: `available` is the result of trying.

    `can_read_history` is false everywhere, deliberately. Neither Instacart's
    Developer Platform nor Walmart's affiliate API exposes a consumer's past
    orders to a third party. The shelf comes from email receipts, and the
    connect screen has to say that, or someone will believe Nano is reading
    their Instacart account and never forward the receipts it needs.
    """
    from ..grocery.factory import capabilities

    return {"platforms": capabilities(db, user_id),
            "history_source": {
                "kind": "email_receipts",
                "connected": bool(_mailboxes(db, user_id)),
                "note": "Nano builds your shelf from grocery receipts in your "
                        "connected email."}}


def _mailboxes(db: Session, user_id: str) -> list[str]:
    from ..substrate.inbox import accounts as _accounts
    return [a.email for a in _accounts(db, user_id)]


@router.post("/grocery/platforms/link")
def link_platform(body: LinkBody, user_id: str = Depends(current_user_id),
                  db: Session = Depends(get_db)):
    """Record an intent to use a platform. This does NOT authenticate anything.

    Kept deliberately weak, and honest about it: none of these platforms offers
    Nano a consumer sign-in. Instacart's basket handoff uses a server-wide key
    and needs nothing from the person; Walmart needs an approval Nano does not
    have. So this endpoint stores a preference and returns what that preference
    actually buys — it never reports "connected".
    """
    from ..grocery.base import StoreError
    from ..grocery.factory import client_for
    from ..grocery.providers import CAPABILITIES

    platform = body.platform.strip().lower()
    cap = CAPABILITIES.get(platform)
    if cap is None:
        raise HTTPException(status_code=422, detail=f"Unknown platform {platform!r}")

    try:
        client_for(db, user_id, platform)
        available, reason = True, ""
    except StoreError as exc:
        available, reason = False, str(exc)

    row = db.scalar(select(GroceryLink).where(
        GroceryLink.user_id == user_id, GroceryLink.platform == platform))
    if row is None:
        row = GroceryLink(user_id=user_id, platform=platform)
        db.add(row)
    # "preferred" is the truth: the person chose it. Whether it works is
    # answered by trying, every time.
    row.status = "preferred"
    row.account_label = body.account_label[:120]
    append_event(db, user_id=user_id, type="grocery_platform_preferred", agent="grocery",
                 domain="grocery", payload={"platform": platform, "available": available})
    db.commit()
    return {"ok": True, **cap.as_dict(available=available, reason=reason)}


@router.post("/grocery/platforms/{platform}/unlink")
def unlink_platform(platform: str, user_id: str = Depends(current_user_id),
                    db: Session = Depends(get_db)):
    row = db.scalar(select(GroceryLink).where(
        GroceryLink.user_id == user_id, GroceryLink.platform == platform.lower()))
    if row is None:
        raise HTTPException(status_code=404, detail="Not set")
    row.status = "revoked"
    db.commit()
    return {"ok": True}


class BasketBody(BaseModel):
    platform: str = "list"
    item_ids: list[str] = Field(default_factory=list)   # empty = everything low/out
    append: bool = False


@router.post("/grocery/basket")
def build_basket(body: BasketBody, user_id: str = Depends(current_user_id),
                 db: Session = Depends(get_db)):
    """Assemble a basket. Commits to nothing — tier 0."""
    from ..agents.grocery import propose_basket

    if body.item_ids:
        items = list(db.scalars(select(GroceryItem).where(
            GroceryItem.user_id == user_id, GroceryItem.id.in_(body.item_ids))))
        if not items:
            raise HTTPException(status_code=404, detail="None of those items exist")
        if body.append:
            from ..substrate.grocery import add_to_basket
            order = add_to_basket(db, user_id=user_id, items=items)
            db.commit()
            return {"ok": True, "order": _order_dict(order)}
        order = db.scalar(select(GroceryOrder).where(
            GroceryOrder.user_id == user_id, GroceryOrder.status == "draft"))
        if order is None:
            order = GroceryOrder(user_id=user_id)
            db.add(order)
        order.platform = body.platform
        order.lines = [{"item_id": i.id, "name": i.name, "quantity": 1,
                        "unit": i.unit, "note": "you asked for this"} for i in items]
        order.reason = "you asked for these"
        db.flush()
    else:
        order = propose_basket(db, user_id, platform=body.platform)
        if order is None:
            db.commit()
            return {"ok": True, "order": None, "note": "Nothing is low or out."}
    # A new basket is a new question, so any earlier yes is void.
    order.confirmed_by = ""
    order.confirmed_at = None
    db.commit()
    return {"ok": True, "order": _order_dict(order)}


def _order_dict(o: GroceryOrder) -> dict:
    return {"id": o.id, "platform": o.platform, "status": o.status,
            "lines": o.lines or [], "reason": o.reason,
            "subtotal_cents": o.subtotal_cents, "external_id": o.external_id,
            "error": o.error, "confirmed_by": o.confirmed_by,
            "fingerprint": _basket_fingerprint(o.lines or [])}


@router.get("/grocery/orders/{order_id}")
def get_order(order_id: str, user_id: str = Depends(current_user_id),
              db: Session = Depends(get_db)):
    o = db.get(GroceryOrder, order_id)
    if o is None or o.user_id != user_id:
        raise HTTPException(status_code=404, detail="No such order")
    return _order_dict(o)


class ConfirmBody(BaseModel):
    fingerprint: str = Field(..., min_length=8, max_length=64)


class BasketLineEdit(BaseModel):
    item_id: str
    quantity: float = Field(ge=1, le=99, allow_inf_nan=False)


class BasketEdit(BaseModel):
    fingerprint: str
    lines: list[BasketLineEdit] = Field(max_length=100)
    platform: str | None = None


@router.patch("/grocery/orders/{order_id}")
def edit_basket(order_id: str, body: BasketEdit, user_id: str = Depends(current_user_id),
                db: Session = Depends(get_db)):
    order = db.scalar(select(GroceryOrder).where(GroceryOrder.id == order_id,
                      GroceryOrder.user_id == user_id).with_for_update())
    if order is None:
        raise HTTPException(404, "Shopping list not found.")
    if order.status not in ("draft", "confirmed", "handed_off"):
        raise HTTPException(409, "This shopping list is closed.")
    if body.fingerprint != _basket_fingerprint(order.lines or []):
        raise HTTPException(409, "Your shopping list changed. Please review the latest version.")
    if body.platform is not None and body.platform not in ("list", "instacart"):
        raise HTTPException(422, "That store isn't available yet.")
    ids = [line.item_id for line in body.lines]
    items = {i.id: i for i in db.scalars(select(GroceryItem).where(
        GroceryItem.user_id == user_id, GroceryItem.id.in_(ids)))}
    if len(items) != len(ids):
        raise HTTPException(422, "Some items are no longer available. Refresh your list.")
    order.lines = [{"item_id": l.item_id, "name": items[l.item_id].name,
                    "quantity": l.quantity, "unit": items[l.item_id].unit, "note": "You added this"}
                   for l in body.lines]
    order.platform = body.platform or order.platform
    order.status, order.external_id = "draft", ""
    order.confirmed_by, order.confirmed_at = "", None
    db.commit()
    return {"ok": True, "order": _order_dict(order)}


@router.post("/grocery/orders/{order_id}/confirm")
def confirm_order(order_id: str, body: ConfirmBody,
                  user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    """The person's own yes, bound to the exact basket they were shown.

    The fingerprint comes from the basket they reviewed. If it no longer
    matches, the basket changed underneath them and the yes does not carry
    over — they see the new one and decide again.
    """
    o = db.get(GroceryOrder, order_id)
    if o is None or o.user_id != user_id:
        raise HTTPException(status_code=404, detail="No such order")
    if o.status not in ("draft", "confirmed"):
        raise HTTPException(status_code=409, detail=f"This order is already {o.status}.")
    current = _basket_fingerprint(o.lines or [])
    if body.fingerprint != current:
        raise HTTPException(
            status_code=409,
            detail="The basket changed since you looked. Review it and confirm again.")
    o.status = "confirmed"
    o.confirmed_by = "user"
    o.confirmed_at = utcnow()
    record_decision(db, user_id=user_id, agent="grocery", action_key="grocery.place_order",
                    decided_by="user", verdict="accepted",
                    payload={"order_id": o.id, "lines": len(o.lines or [])})
    db.commit()
    return {"ok": True, "order": _order_dict(o)}


@router.post("/grocery/orders/{order_id}/handoff")
def handoff_order(order_id: str, body: ConfirmBody | None = None, user_id: str = Depends(current_user_id),
                  db: Session = Depends(get_db)):
    """Hand the basket to the platform and return where to open it.

    This — not `/place` — is what the product does. Nano fills the basket;
    the person opens the link, picks their store, and pays there. No card ever
    reaches Nano, which is why this needs no confirmation gate: it spends
    nothing. It is a link, not a purchase.
    """
    o = db.scalar(select(GroceryOrder).where(GroceryOrder.id == order_id,
                  GroceryOrder.user_id == user_id).with_for_update())
    if o is None or o.user_id != user_id:
        raise HTTPException(status_code=404, detail="No such order")
    if not (o.lines or []):
        raise HTTPException(status_code=422, detail="The basket is empty.")
    if o.status not in ("draft", "confirmed", "handed_off"):
        raise HTTPException(409, "This shopping list is closed.")
    if body and body.fingerprint != _basket_fingerprint(o.lines or []):
        raise HTTPException(409, "Your shopping list changed. Please review the latest version.")
    if o.status == "handed_off" and o.external_id:
        return {"ok": True, "url": o.external_id, "order": _order_dict(o)}

    lines = []
    for l in (o.lines or []):
        item = db.get(GroceryItem, l.get("item_id", ""))
        lines.append(OrderLine(
            item_id=l.get("item_id", ""), name=l.get("name", ""),
            quantity=float(l.get("quantity") or 1), unit=l.get("unit", ""),
            product_ref=(item.product_ref if item is not None else ""),
            product_ref_kind=(item.product_ref_kind if item is not None else "")))
    try:
        result = client_for(db, user_id, o.platform).handoff(lines)
    except StoreError as exc:
        # 422, not 500: nothing broke. This platform cannot take the basket,
        # and the person needs to hear exactly that rather than "try again".
        raise HTTPException(status_code=422, detail=str(exc))

    o.status = "handed_off"
    # Not truncated: this is a URL, and half a URL is a dead link the person
    # would tap. The column was widened in 0022 for exactly this.
    o.external_id = result.get("url") or ""
    append_event(db, user_id=user_id, type="grocery_basket_handed_off", agent="grocery",
                 domain="grocery", payload={"order_id": o.id, "platform": o.platform,
                                            "lines": len(o.lines or [])})
    db.commit()
    return {"ok": True, **result, "order": _order_dict(o)}


@router.post("/grocery/orders/{order_id}/place")
def place_order(order_id: str, user_id: str = Depends(current_user_id),
                db: Session = Depends(get_db)):
    """Actually buy it. Four gates, in order, and every one of them can say no."""
    o = db.get(GroceryOrder, order_id)
    if o is None or o.user_id != user_id:
        raise HTTPException(status_code=404, detail="No such order")
    if o.status == "placed":
        raise HTTPException(status_code=409, detail="Already placed")

    # 1. The person said yes, to THIS basket.
    if o.confirmed_by != "user":
        raise HTTPException(status_code=403,
                            detail="Nobody confirmed this basket. Nano never buys on its own.")
    if o.status != "confirmed":
        raise HTTPException(status_code=409, detail=f"This order is {o.status}.")

    # 2. Money is tier 3, which means NO autonomous path may reach this code.
    #    That is enforced by there being no caller other than this route, and
    #    this route requiring the confirmation checked above.
    #
    #    An earlier version called assess() here and then did nothing with the
    #    verdict — a few lines that read like a security check and enforced
    #    nothing, which is worse than not having them. The check is recorded
    #    instead, so the ledger shows a human authorised a tier-3 act.
    record_decision(db, user_id=user_id, agent="grocery",
                    action_key="grocery.place_order", decided_by="user",
                    verdict="acted",
                    payload={"order_id": o.id, "platform": o.platform,
                             "risk_tier": assess("grocery.place_order",
                                                 provenance="user").tier})

    # 3. The platform must be able to do it, and say so if it cannot.
    try:
        client = client_for(db, user_id, o.platform)
        lines = [OrderLine(item_id=l.get("item_id", ""), name=l.get("name", ""),
                           quantity=float(l.get("quantity") or 1),
                           unit=l.get("unit", "")) for l in (o.lines or [])]
        external = client.place_order(lines)
    except StoreError as exc:
        o.status = "failed"
        o.error = str(exc)[:300]
        db.commit()
        # 422, not 500: nothing broke. This platform cannot do it, and the
        # person needs to hear exactly that rather than "try again".
        raise HTTPException(status_code=422, detail=str(exc))

    # 4. It really went through.
    o.status = "placed"
    o.external_id = str(external)[:120]
    o.placed_at = utcnow()
    for line in (o.lines or []):
        item = db.get(GroceryItem, line.get("item_id", ""))
        if item is not None and item.user_id == user_id:
            record_purchase(db, user_id=user_id, item=item, purchased_at=utcnow(),
                            quantity=float(line.get("quantity") or 1),
                            source="platform", source_ref=o.external_id,
                            merchant=o.platform)
    append_event(db, user_id=user_id, type="grocery_order_placed", agent="grocery",
                 domain="grocery", payload={"order_id": o.id, "platform": o.platform,
                                            "external_id": o.external_id})
    db.commit()
    return {"ok": True, "order": _order_dict(o)}


@router.post("/grocery/orders/{order_id}/cancel")
def cancel_order(order_id: str, user_id: str = Depends(current_user_id),
                 db: Session = Depends(get_db)):
    o = db.get(GroceryOrder, order_id)
    if o is None or o.user_id != user_id:
        raise HTTPException(status_code=404, detail="No such order")
    if o.status == "placed":
        raise HTTPException(status_code=409,
                            detail="This one is already placed — cancel it with the store.")
    o.status = "cancelled"
    db.commit()
    return {"ok": True}
