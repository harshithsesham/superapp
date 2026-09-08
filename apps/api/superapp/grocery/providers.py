"""The platforms, and an honest account of what each one actually gives you.

The first cut of this file let `/platforms/link` write `status="linked"` with
nothing behind it — no authentication, no token, no fetch. The screen then said
"Linked" for a connection that did not exist. That is the precise failure the
seam next door was built to prevent, committed by the module that quotes the
rule. It is fixed here by making capability explicit rather than boolean:

    can_handoff       build a basket the person opens and pays for
    can_read_history  fetch what they have bought before
    needs_auth        requires the person to sign in

Nothing reads purchase history. Neither Instacart's Developer Platform nor
Walmart's affiliate API exposes a consumer's past orders to a third party, so
`can_read_history` is False everywhere and the shelf is built from EMAIL
RECEIPTS. The connect screen must say so, because a person who believes Nano
is reading their Instacart account will not forward the receipts it actually
needs.
"""
import httpx

from ..config import get_settings
from .base import OrderLine, Quote, StoreNotConnected, StoreUnsupported

INSTACART_API = "https://connect.instacart.com/idp/v1/products/products_link"


class Capability:
    """What a platform can do for this user, as data the UI can render."""

    def __init__(self, *, key: str, label: str, can_handoff: bool,
                 can_read_history: bool, needs_auth: bool, note: str):
        self.key, self.label = key, label
        self.can_handoff = can_handoff
        self.can_read_history = can_read_history
        self.needs_auth = needs_auth
        self.note = note

    def as_dict(self, *, available: bool, reason: str = "") -> dict:
        return {"platform": self.key, "label": self.label,
                "can_handoff": self.can_handoff and available,
                "can_read_history": self.can_read_history,
                "needs_auth": self.needs_auth,
                "available": available,
                "note": self.note, "unavailable_reason": reason}


CAPABILITIES = {
    "list": Capability(
        key="list", label="Shopping list", can_handoff=True,
        can_read_history=False, needs_auth=False,
        note="A list you shop from. Nothing is ordered."),
    "instacart": Capability(
        key="instacart", label="Instacart", can_handoff=True,
        can_read_history=False, needs_auth=False,
        note="Nano fills a basket and hands you a link. You pick the store and "
             "pay on Instacart. It cannot see your past Instacart orders."),
    "walmart": Capability(
        key="walmart", label="Walmart", can_handoff=False,
        can_read_history=False, needs_auth=False,
        note="Walmart shopping is not available yet. Your shopping list stays saved in Nano."),
}


class ListStore:
    """No platform at all: the basket becomes a list.

    Always available, because a shopping list is something the app can always
    honestly deliver.
    """

    platform = "list"
    can_order = False

    def quote(self, lines: list[OrderLine]) -> Quote:
        return Quote(platform=self.platform, lines=list(lines),
                     note="A list to shop from. Nano is not buying anything here.")

    def handoff(self, lines: list[OrderLine]) -> dict:
        return {"platform": self.platform, "url": "",
                "note": "Shop from the list — there is nothing to hand off to."}

    def place_order(self, lines: list[OrderLine]) -> str:
        raise StoreUnsupported(
            "This is a shopping list, not a store. Nano does not check out for you.")


class InstacartStore:
    """Instacart Developer Platform: a basket, handed over.

    `products_link` takes line items — names, quantities, and UPCs or product
    ids where we have them — and returns a URL. The person opens it, chooses a
    store, reviews what matched, and checks out on Instacart. Nano never holds
    a card and never places the order.

    Pinning by UPC is why reading receipts matters: a recipe app sends "milk"
    and hopes; we send the UPC off this household's own receipt.
    """

    platform = "instacart"
    can_order = False          # handing over a basket is not checking out

    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key or get_settings().instacart_api_key
        if not self.api_key:
            raise StoreNotConnected(
                "Instacart is unavailable right now. Your shopping list is saved.")

    @staticmethod
    def _line(line: OrderLine) -> dict:
        item: dict = {"name": line.name, "quantity": max(float(line.quantity or 1), 1)}
        if line.unit:
            item["unit"] = line.unit
        # Exactly one identifier: the API treats UPC and product id as
        # mutually exclusive, and sending both is a request it will reject.
        if line.product_ref and line.product_ref_kind == "upc":
            item["upcs"] = [line.product_ref]
        elif line.product_ref and line.product_ref_kind == "id" and line.product_ref.isdigit():
            item["product_ids"] = [int(line.product_ref)]
        # An unusable ref sends no identifier at all rather than an empty list:
        # the name still matches, and an empty array is a request Instacart has
        # no reason to accept.
        return item

    def quote(self, lines: list[OrderLine]) -> Quote:
        # No invented prices. Instacart prices vary by store, and the person
        # has not chosen one yet; a total they mistake for real is worse than
        # no total at all.
        return Quote(platform=self.platform, lines=list(lines),
                     note="Prices depend on the store you pick on Instacart.")

    def handoff(self, lines: list[OrderLine], *, title: str = "Your Nano basket") -> dict:
        if not lines:
            raise StoreUnsupported("There is nothing in the basket to hand over.")
        payload = {"title": title, "link_type": "shopping_list",
                   "line_items": [self._line(l) for l in lines]}
        try:
            resp = httpx.post(INSTACART_API, json=payload, timeout=20,
                              headers={"Authorization": f"Bearer {self.api_key}",
                                       "Content-Type": "application/json"})
            resp.raise_for_status()
            url = (resp.json() or {}).get("products_link_url", "")
        except httpx.HTTPError as exc:
            # Loud. A handoff that silently produced no link would leave the
            # person believing a basket is waiting for them.
            raise StoreNotConnected("Couldn’t open Instacart right now. Your shopping list is saved; please try again.") from exc
        if not url:
            raise StoreNotConnected("Instacart returned no basket link.")
        return {"platform": self.platform, "url": url,
                "note": "Opens on Instacart with the basket filled. You pick the "
                        "store and pay there."}

    def place_order(self, lines: list[OrderLine]) -> str:
        raise StoreUnsupported(
            "Review your shopping list and complete checkout on Instacart. "
            "Open the link and pay on Instacart.")


class WalmartStore:
    """Declared, not pretended.

    Walmart's add-to-cart endpoint is open to Impact Radius publishers only.
    Until that approval exists there is nothing to connect, and saying
    "Linked" would be a lie the person acts on.
    """

    platform = "walmart"
    can_order = False

    def __init__(self, *_a, **_kw) -> None:
        raise StoreNotConnected(
            "Walmart shopping is not available yet. Your shopping list is saved.")


PLATFORMS = {k: (c.label, c.needs_auth) for k, c in CAPABILITIES.items()}
