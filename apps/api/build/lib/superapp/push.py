"""Pushes — Nano reaching the lock screen (north star step 4).

Direct APNs first (device token registered by the app, .p8 on the server);
legacy Expo-push token as fallback. A hard attention floor applies: never more
than settings.max_pushes_per_day interruptions, whatever the source — the full
attention budget arrives with step 5, but the cap is not negotiable even now.
Every send (and every suppression) is logged to events.
"""
from datetime import datetime, time as dtime, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .apns import send_apns
from .config import get_settings
from .models import Event
from .substrate import append_event, read_facts

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


def _fact(db: Session, user_id: str, key: str) -> str | None:
    facts = read_facts(db, user_id=user_id, domains=["system"], limit=10)
    fact = next((f for f in facts if f.key == key), None)
    return fact.value.get("token") if fact else None


def _pushes_today(db: Session, user_id: str) -> int:
    day_start = datetime.combine(datetime.now(timezone.utc).date(), dtime.min, tzinfo=timezone.utc)
    return db.scalar(
        select(func.count()).select_from(Event).where(
            Event.user_id == user_id, Event.type == "push_sent",
            Event.created_at >= day_start)
    ) or 0


def live_activity(db: Session, *, user_id: str, event: str, state: dict,
                  title: str | None = None) -> bool:
    """Drive the lock-screen Live Activity from the server. Start uses the
    push-to-start token; update/end use the freshest per-activity update
    token the app reported. Silent no-op when no token or no APNs key —
    the app's own local activity (when open) still works."""
    from .apns import send_liveactivity

    key = ("liveactivity_start_token" if event == "start"
           else "liveactivity_update_token")
    token = _fact(db, user_id, key)
    if not token:
        return False
    ok = send_liveactivity(token=token, event=event, state=state, title=title)
    append_event(db, user_id=user_id, type="live_activity", agent="scout",
                 payload={"event": event, "ok": ok})
    return ok


def send_push(db: Session, *, user_id: str, title: str, body: str, agent: str | None = None) -> bool:
    settings = get_settings()
    if _pushes_today(db, user_id) >= settings.max_pushes_per_day:
        append_event(db, user_id=user_id, type="push_suppressed", agent=agent,
                     payload={"title": title, "reason": "daily attention cap"})
        return False

    ok = False
    apns_token = _fact(db, user_id, "apns_device_token")
    if apns_token:
        ok = send_apns(device_token=apns_token, title=title, body=body)
    if not ok:
        expo_token = _fact(db, user_id, "expo_push_token")
        if expo_token:
            try:
                httpx.post(EXPO_PUSH_URL,
                           json={"to": expo_token, "title": title, "body": body,
                                 "sound": "default"},
                           timeout=10).raise_for_status()
                ok = True
            except httpx.HTTPError:
                ok = False
    if not (apns_token or _fact(db, user_id, "expo_push_token")):
        return False  # no device registered: silent no-op, not an event
    append_event(db, user_id=user_id, type="push_sent", agent=agent,
                 payload={"title": title, "ok": ok, "via": "apns" if apns_token else "expo"})
    return ok
