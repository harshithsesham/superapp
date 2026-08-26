"""Google sign-in sessions: user provisioning + bearer session issuance.

complete_signin() is the trust boundary: it is only called with identity fields
that came directly from Google's token endpoint over TLS (or from tests).
"""
import hashlib
import re
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import AuthSession, User, utcnow


def _email_links() -> dict[str, str]:
    return {
        email.strip().lower(): uid.strip()
        for pair in get_settings().user_email_links.split(",")
        if (email := pair.partition(":")[0]) and (uid := pair.partition(":")[2])
    }


def _mint_user_id(db: Session, email: str) -> str:
    base = re.sub(r"[^a-z0-9]", "", email.split("@")[0].lower())[:24] or "user"
    candidate, n = base, 1
    while db.get(User, candidate) is not None:
        n += 1
        candidate = f"{base}{n}"
    return candidate


def complete_signin(db: Session, *, google_sub: str, email: str, name: str) -> tuple[User, str]:
    """Find-or-create the user, issue a session. Returns (user, raw_token)."""
    email = email.lower()
    user = db.scalar(select(User).where(User.google_sub == google_sub))
    if user is None:
        user = db.scalar(select(User).where(User.email == email))
        if user is not None:
            user.google_sub = google_sub  # email pre-created/linked; anchor the sub
    if user is None:
        linked_id = _email_links().get(email)
        user = User(
            id=linked_id or _mint_user_id(db, email),
            google_sub=google_sub, email=email, name=name,
        )
        db.add(user)
        db.flush()

    token = secrets.token_urlsafe(32)
    db.add(AuthSession(
        token_hash=hashlib.sha256(token.encode()).hexdigest(), user_id=user.id,
    ))
    db.flush()
    return user, token


def resolve_session(db: Session, bearer: str) -> str | None:
    """Bearer -> user_id, or None. Touches last_used."""
    row = db.get(AuthSession, hashlib.sha256(bearer.encode()).hexdigest())
    if row is None:
        return None
    row.last_used_at = utcnow()
    return row.user_id
