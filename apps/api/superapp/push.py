"""Expo push notifications — for insights worth interrupting for (roadmap Phase 2).

The phone registers its token via POST /v1/devices/push-token (stored as a
system fact). No token registered = silent no-op, so everything degrades
gracefully in Expo Go / stub setups. Every send is logged to events.
"""
import httpx
from sqlalchemy.orm import Session

from .substrate import append_event, read_facts

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


def _push_token(db: Session, user_id: str) -> str | None:
    facts = read_facts(db, user_id=user_id, domains=["system"], limit=10)
    fact = next((f for f in facts if f.key == "expo_push_token"), None)
    return fact.value.get("token") if fact else None


def send_push(db: Session, *, user_id: str, title: str, body: str, agent: str | None = None) -> bool:
    token = _push_token(db, user_id)
    if not token:
        return False
    ok = True
    try:
        httpx.post(
            EXPO_PUSH_URL,
            json={"to": token, "title": title, "body": body, "sound": "default"},
            timeout=10,
        ).raise_for_status()
    except httpx.HTTPError:
        ok = False
    append_event(
        db, user_id=user_id, type="push_sent", agent=agent,
        payload={"title": title, "ok": ok},
    )
    return ok
