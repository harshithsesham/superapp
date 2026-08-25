"""Bearer auth with a token -> user map. The multi-tenant swap point
(architecture §8): sign-in providers replace this module later; nothing below
it ever assumed one user.
"""
import hmac

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import get_settings

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


def current_user_id(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    if creds is not None:
        for token, user_id in token_map().items():
            if hmac.compare_digest(creds.credentials, token):
                return user_id
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
