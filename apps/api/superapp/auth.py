"""Bearer auth: static token map (founders, crons, dev) + Google sign-in
sessions. The multi-tenant story lives here and in auth_sessions.py.
"""
import hmac

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .auth_sessions import resolve_session
from .config import get_settings
from .db import get_db

_bearer = HTTPBearer(auto_error=False)


def token_map() -> dict[str, str]:
    """token -> user_id. user_tokens ("alice:t1,bob:t2") plus the legacy pair."""
    settings = get_settings()
    m: dict[str, str] = {}
    for pair in settings.user_tokens.split(","):
        user, _, token = pair.strip().partition(":")
        if user and token:
            m[token] = user
    if settings.api_token:
        m.setdefault(settings.api_token, settings.default_user_id)
    return m


def resolve_token(db: Session, token: str) -> str | None:
    """Resolve a raw bearer value to a user_id (static map, then sessions)."""
    for known, user_id in token_map().items():
        if hmac.compare_digest(token, known):
            return user_id
    user = resolve_session(db, token)
    if user is not None:
        db.commit()
    return user


def current_user_id(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> str:
    if creds is not None:
        for token, user_id in token_map().items():
            if hmac.compare_digest(creds.credentials, token):
                return user_id
        session_user = resolve_session(db, creds.credentials)
        if session_user is not None:
            db.commit()  # persist last_used touch
            return session_user
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
