"""WhatsApp channel gateway (Nano 2.0 Phase C) — Twilio-backed.

Same shape as the Telegram gateway: one webhook receives messages, pipes
them through the converse brain (people graph, inbox actions, flight
watches, campaigns, kernel all apply unchanged), and replies through
Twilio's Messages API. Twilio posts form-encoded bodies and signs every
request with X-Twilio-Signature (HMAC-SHA1 over URL + sorted params with
the auth token); we verify that AND embed a token-derived secret in the
path. Number->user pairing is an env map, like Telegram's.

Dormant until twilio_account_sid / twilio_auth_token /
twilio_whatsapp_from are configured.
"""
import base64
import hashlib
import hmac
from collections import defaultdict, deque

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db

router = APIRouter(prefix="/v1/whatsapp", tags=["whatsapp"])

_history: dict[str, deque] = defaultdict(lambda: deque(maxlen=16))


def _webhook_secret() -> str:
    token = get_settings().twilio_auth_token
    return hashlib.sha256(f"wa:{token}".encode()).hexdigest()[:24] if token else ""


def _chat_map() -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in get_settings().whatsapp_chats.split(","):
        pair = pair.strip()
        if ":" in pair:
            number, user = pair.split(":", 1)
            if number.strip():
                out[number.strip()] = user.strip()
    return out


def _valid_signature(url: str, params: dict, signature: str) -> bool:
    """Twilio's scheme: base64(HMAC-SHA1(auth_token, url + k1v1k2v2... sorted))."""
    token = get_settings().twilio_auth_token
    payload = url + "".join(k + str(v) for k, v in sorted(params.items()))
    digest = hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)


def _send(to: str, text: str) -> None:
    settings = get_settings()
    if not (settings.twilio_account_sid and settings.twilio_auth_token
            and settings.twilio_whatsapp_from):
        return
    try:
        httpx.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json",
            auth=(settings.twilio_account_sid, settings.twilio_auth_token),
            data={"From": settings.twilio_whatsapp_from, "To": to,
                  "Body": text[:1500]},
            timeout=15)
    except httpx.HTTPError:
        pass  # Twilio retries the webhook; the reply just missed one beat


@router.post("/webhook/{secret}")
async def whatsapp_webhook(secret: str, request: Request,
                           db: Session = Depends(get_db)):
    expected = _webhook_secret()
    if not expected or not hmac.compare_digest(secret, expected):
        raise HTTPException(status_code=403, detail="Bad webhook secret")
    form = dict(await request.form())
    settings = get_settings()
    if settings.scout_public_base:
        # Signature is REQUIRED, not optional — omitting the header must
        # not skip the check, or forgery costs one leaked log line.
        sig = request.headers.get("X-Twilio-Signature", "")
        url = f"{settings.scout_public_base}/v1/whatsapp/webhook/{secret}"
        if not sig or not _valid_signature(url, form, sig):
            raise HTTPException(status_code=403, detail="Bad signature")

    sender = str(form.get("From", ""))          # "whatsapp:+14155551234"
    text = str(form.get("Body", "")).strip()
    number = sender.removeprefix("whatsapp:")
    if not number or not text:
        return _twiml()

    user_id = _chat_map().get(number)
    if user_id is None:
        _send(sender,
              f"Hi, I'm Nano, but this number isn't paired yet. "
              f"Your number is {number}. Add it on the server "
              f"(SUPERAPP_WHATSAPP_CHATS) and message me again.")
        return _twiml()

    # Same brain as the orb and Telegram: reuse the converse pipeline.
    from .voice import ConverseBody, Turn, converse

    _history[number].append({"role": "user", "text": text[:4000]})
    try:
        body = ConverseBody(messages=[Turn(**t) for t in _history[number]])
        result = converse(body, user_id=user_id, db=db)
        say = result.get("say") or "…"
    except Exception:  # noqa: BLE001
        say = "I hit a snag just now, try that again in a moment."
    _history[number].append({"role": "nano", "text": say[:4000]})
    _send(sender, say)
    return _twiml()


def _twiml() -> Response:
    """Twilio wants TwiML (or empty XML) back, not JSON — a JSON reply logs
    error 12300 on every message. Replies go out via the REST API instead."""
    return Response(content="<Response/>", media_type="text/xml")
