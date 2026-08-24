"""Single-user bearer auth (Phase 0). Multi-tenant swap point later (architecture §8)."""
import hmac

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import get_settings

_bearer = HTTPBearer(auto_error=False)


def current_user_id(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    settings = get_settings()
    if creds is None or not hmac.compare_digest(creds.credentials, settings.api_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return settings.default_user_id
