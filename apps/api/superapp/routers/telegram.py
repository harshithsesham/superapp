"""Telegram channel gateway — Nano in your pocket without opening the app.

One webhook receives messages from the user's own Telegram bot and pipes
them through the same conversational brain as the orb and the Flights chat
(/v1/voice/converse internals): people graph, inbox actions, flight
watches, and the kernel all apply unchanged. Replies go back through the
Bot API. Chat->user pairing is an env map for now
(SUPERAPP_TELEGRAM_CHATS="123456:harshith,789:cofounder").

The webhook path embeds a secret derived from the bot token, so only
Telegram (which knows the token) can reach it.
"""
import hashlib
import json
from collections import defaultdict, deque

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request  # noqa: F401
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db

router = APIRouter(prefix="/v1/telegram", tags=["telegram"])

# Rolling per-chat history (single worker; best-effort like pending-actions).
_history: dict[int, deque] = defaultdict(lambda: deque(maxlen=16))


def _webhook_secret() -> str:
    token = get_settings().telegram_bot_token
    return hashlib.sha256(f"tg:{token}".encode()).hexdigest()[:24] if token else ""


def _chat_map() -> dict[int, str]:
    raw = get_settings().telegram_chats
    out: dict[int, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if ":" in pair:
            chat, user = pair.split(":", 1)
            try:
                out[int(chat.strip())] = user.strip()
            except ValueError:
                continue
    return out


def _send(chat_id: int, text: str) -> None:
    token = get_settings().telegram_bot_token
    if not token:
        return
    try:
        httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                   json={"chat_id": chat_id, "text": text[:4000]}, timeout=15)
    except httpx.HTTPError:
        pass  # Telegram retries the webhook; the reply just missed one beat


@router.post("/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request, db: Session = Depends(get_db)):
    expected = _webhook_secret()
    if not expected or secret != expected:
        raise HTTPException(status_code=403, detail="Bad webhook secret")
    update = await request.json()
    msg = update.get("message") or update.get("edited_message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    text = (msg.get("text") or "").strip()
    if not chat_id or not text:
        return {"ok": True}

    user_id = _chat_map().get(chat_id)
    if user_id is None:
        _send(chat_id,
              f"Hi — I'm Nano, but this chat isn't paired yet.\n"
              f"Your chat id is {chat_id}. Add it on the server "
              f"(SUPERAPP_TELEGRAM_CHATS) and message me again.")
        return {"ok": True}

    if text.startswith("/start"):
        _send(chat_id, "Here. Ask me anything — your inbox, flights, "
                       "calories, or an errand for the scout.")
        return {"ok": True}

    # Same brain as the orb: reuse the converse pipeline verbatim.
    from .voice import ConverseBody, Turn, converse

    _history[chat_id].append({"role": "user", "text": text[:4000]})
    try:
        body = ConverseBody(messages=[Turn(**t) for t in _history[chat_id]])
        result = converse(body, user_id=user_id, db=db)
        say = result.get("say") or "…"
    except Exception:  # noqa: BLE001
        say = "I hit a snag just now — try that again in a moment."
    _history[chat_id].append({"role": "nano", "text": say[:4000]})
    _send(chat_id, say)
    return {"ok": True}

