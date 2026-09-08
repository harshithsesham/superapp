"""The shopping-platform seam.

Modelled on the mail seam next door, for the same reason: everything above
this line deals in a `StoreClient`, never in Walmart. A platform is a class
implementing this protocol plus an entry in `factory.client_for`.

One rule this protocol exists to enforce, because getting it wrong is the
worst bug this vertical could ship:

    A client that cannot place an order must RAISE. It must never return a
    plausible-looking order id.

The mail seam learned this the hard way — a mailbox with no token fell back to
the offline client and reported imaginary sends as real. Here the same mistake
costs a person their actual groceries: they see "ordered", they stop thinking
about it, and nothing arrives. `StoreNotConnected` exists so that failure is
loud on the day it happens rather than at dinner time.
"""
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


class StoreError(Exception):
    """Base for platform problems the app is expected to handle."""


class StoreNotConnected(StoreError):
    """No usable credential for this platform, or it was never linked.

    Raised INSTEAD of pretending. A grocery order that cannot be placed must
    fail where the person can see it.
    """


class StoreUnsupported(StoreError):
    """The platform is linked but does not expose the operation.

    Walmart and Instacart publish partner APIs for retailers and advertisers,
    not consumer order-placement APIs for third-party apps. Until Nano holds
    such a partnership, ordering through them is unsupported — a fact about
    the world, not a bug to route around. Saying so plainly beats a scraped
    checkout flow that breaks silently and spends real money when it misfires.
    """


@dataclass
class OrderLine:
    """One thing to buy. `item_id` links back to the shelf.

    `product_ref` is a UPC or a store product id where we know one, so a
    handoff basket contains the exact product this household buys rather than
    a store's best guess at the word "milk".
    """
    item_id: str
    name: str
    quantity: float = 1
    unit: str = ""
    note: str = ""
    product_ref: str = ""
    product_ref_kind: str = ""       # upc | id


@dataclass
class Quote:
    """What a basket would cost, before anyone commits to anything."""
    platform: str
    lines: list[OrderLine] = field(default_factory=list)
    subtotal_cents: int | None = None
    currency: str = "USD"
    unavailable: list[str] = field(default_factory=list)
    note: str = ""


@runtime_checkable
class StoreClient(Protocol):
    """What the grocery vertical needs from a shopping platform."""

    platform: str
    can_order: bool          # False = list-only; the app must not offer a Place button

    def quote(self, lines: list[OrderLine]) -> Quote:
        """Price a basket. Read-only, spends nothing, commits to nothing."""
        ...

    def handoff(self, lines: list[OrderLine]) -> dict:
        """Hand the basket to the platform and return where to open it.

        This is what the real platforms actually offer, and what the product
        promises: Nano fills the basket, the person opens it and pays. Returns
        {"platform", "url", "note"}; an empty url means "there is nowhere to
        send you", never a broken link.
        """
        ...

    def place_order(self, lines: list[OrderLine]) -> str:
        """Actually buy it. Returns the platform's order id.

        Only ever called after a person has confirmed this exact basket:
        `grocery.place_order` is a tier-3 action, so no autonomous path
        reaches here. A client that cannot do this raises rather than
        returning something that looks like success.
        """
        ...
