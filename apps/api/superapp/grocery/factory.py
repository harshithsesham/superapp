"""The one place that turns a link row into a working store client."""
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import GroceryLink
from ..vault import get_token
from .base import StoreClient, StoreError, StoreNotConnected
from .providers import InstacartStore, ListStore, WalmartStore

_CLASSES = {"walmart": WalmartStore, "instacart": InstacartStore}


def capabilities(db: Session, user_id: str) -> list[dict]:
    """What each platform can actually do for this person, right now.

    `available` is decided by trying to build the client, not by a row in a
    table saying "linked". A status that cannot be wrong is better than one
    that has to be kept in sync.
    """
    from .providers import CAPABILITIES
    out = []
    for key, cap in CAPABILITIES.items():
        try:
            client_for(db, user_id, key)
            out.append(cap.as_dict(available=True))
        except StoreError as exc:
            out.append(cap.as_dict(available=False, reason=str(exc)))
    return out


def links(db: Session, user_id: str) -> list[GroceryLink]:
    return list(db.scalars(select(GroceryLink).where(GroceryLink.user_id == user_id)
                           .order_by(GroceryLink.created_at)))


def client_for(db: Session, user_id: str, platform: str) -> StoreClient:
    """A client for one platform, or a raise. Never a silent fallback to the
    list — "I added it to your list" when the person asked to order is a
    different outcome, and they have to be told which one happened."""
    platform = (platform or "list").strip().lower()
    if platform == "list":
        return ListStore()
    cls = _CLASSES.get(platform)
    if cls is None:
        raise StoreNotConnected(f"Nano doesn't know a store called {platform!r}.")
    if platform == "instacart":
        # Server-wide credential: the handoff API builds a shareable basket and
        # needs no consumer sign-in, so there is nothing per-user to link. The
        # constructor raises when the key is absent.
        return cls()
    raw = get_token(db, user_id=user_id, provider=f"grocery:{platform}")
    return cls(json.loads(raw) if raw else None)
