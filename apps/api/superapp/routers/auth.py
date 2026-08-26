"""Google sign-in (identity only: openid email profile — no mail access).

GET /v1/auth/google/start     -> 302 to Google's consent screen
GET /v1/auth/google/callback  -> verify with Google, provision user, issue a
                                 session, bounce back into the app via deep link
"""
import hashlib
import hmac as hmac_mod
import time
import urllib.parse

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..auth_sessions import complete_signin
from ..config import get_settings
from ..db import get_db

router = APIRouter(prefix="/v1/auth", tags=["auth"])

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"


def _state() -> str:
    ts = str(int(time.time()))
    sig = hmac_mod.new(get_settings().api_token.encode(), f"signin:{ts}".encode(),
                       hashlib.sha256).hexdigest()[:24]
    return f"{ts}.{sig}"


def _check_state(state: str) -> None:
    ts, _, _ = state.partition(".")
    if not ts.isdigit() or abs(time.time() - int(ts)) > 600 or not hmac_mod.compare_digest(
        _state_for(ts), state
    ):
        raise HTTPException(status_code=403, detail="Bad sign-in state")


def _state_for(ts: str) -> str:
    sig = hmac_mod.new(get_settings().api_token.encode(), f"signin:{ts}".encode(),
                       hashlib.sha256).hexdigest()[:24]
    return f"{ts}.{sig}"


@router.get("/google/start")
def google_start():
    settings = get_settings()
    if not settings.google_client_id:
        raise HTTPException(status_code=400, detail="Google sign-in not configured")
    params = urllib.parse.urlencode({
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_signin_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": _state(),
        "prompt": "select_account",
    })
    return RedirectResponse(f"{GOOGLE_AUTH}?{params}")


@router.get("/google/callback")
def google_callback(code: str, state: str = "", db: Session = Depends(get_db)):
    _check_state(state)
    settings = get_settings()
    token_data = httpx.post(GOOGLE_TOKEN, data={
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "redirect_uri": settings.google_signin_redirect_uri,
        "grant_type": "authorization_code", "code": code,
    }, timeout=30).raise_for_status().json()
    # Direct TLS channel to Google: userinfo is authoritative for this token.
    info = httpx.get(GOOGLE_USERINFO, timeout=30, headers={
        "Authorization": f"Bearer {token_data['access_token']}",
    }).raise_for_status().json()
    if not info.get("email_verified", False):
        raise HTTPException(status_code=403, detail="Unverified Google email")

    user, session_token = complete_signin(
        db, google_sub=info["sub"], email=info["email"], name=info.get("name", ""),
    )
    db.commit()
    q = urllib.parse.urlencode({"token": session_token, "user": user.id, "name": user.name})
    return RedirectResponse(f"superapp://signed-in?{q}")
