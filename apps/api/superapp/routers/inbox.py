"""Nano inbox endpoints (Phase 3).

Connect: /inbox/connect/stub for the offline mailbox; the OAuth pair
(/gmail/auth-url + /gmail/callback) for real Gmail. The Pub/Sub webhook is
Google-facing (secret path token, no bearer). Draft actions are the only way
mail ever leaves: send requires an explicit tap AND scope tier >= send.
"""
import base64
import hashlib
import hmac as hmac_mod
import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..agents.base import render_screen, run_think
from ..auth import current_user_id
from ..config import get_settings
from ..db import get_db
from ..inbox.gmail_client import GmailClient
from ..models import InboxMessage
from ..substrate import append_event
from ..substrate.inbox import get_draft, upsert_account
from ..vault import get_token, store_token

router = APIRouter(prefix="/v1", tags=["inbox"])


def _connect(db: Session, *, user_id: str, email: str, token: dict) -> dict:
    store_token(db, user_id=user_id, provider=f"gmail:{email}", token=json.dumps(token))
    acct = upsert_account(db, user_id=user_id, email=email)
    client = GmailClient(token)
    expiry = client.watch()  # register Pub/Sub push where configured
    if expiry:
        acct.watch_expiry = expiry
    append_event(db, user_id=user_id, type="gmail_connected", agent="inbox", domain="inbox",
                 payload={"email": email})
    # Initial backfill + triage (the onboarding "scan" moment).
    run_think(db, agent="inbox", user_id=user_id, trigger={"kind": "email_sync", "reason": "backfill"})
    return render_screen(db, agent="inbox", user_id=user_id).model_dump()


@router.post("/inbox/connect/stub")
def connect_stub(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    if not GmailClient().stubbed:
        raise HTTPException(status_code=400, detail="Live Gmail configured; use /v1/gmail/auth-url")
    return _connect(db, user_id=user_id, email="stub@example.com", token={"stub": True})


def _sign_state(user_id: str) -> str:
    key = get_settings().api_token.encode()
    sig = hmac_mod.new(key, user_id.encode(), hashlib.sha256).hexdigest()[:24]
    return f"{user_id}.{sig}"


def _verify_state(state: str) -> str:
    user_id, _, sig = state.rpartition(".")
    if not user_id or not hmac_mod.compare_digest(_sign_state(user_id), state):
        raise HTTPException(status_code=403, detail="Bad OAuth state")
    return user_id


@router.get("/gmail/auth-url")
def gmail_auth_url(user_id: str = Depends(current_user_id)):
    client = GmailClient()
    if client.stubbed:
        raise HTTPException(status_code=400, detail="Set SUPERAPP_GOOGLE_CLIENT_ID first")
    return {"auth_url": client.auth_url(state=_sign_state(user_id))}


@router.get("/gmail/callback")
def gmail_callback(code: str, state: str = "", db: Session = Depends(get_db)):
    """OAuth redirect target (browser; Google can't send our bearer). Identity
    comes from the HMAC-signed state we generated in auth-url."""
    user_id = _verify_state(state)
    client = GmailClient()
    token = client.exchange_code(code)
    email = GmailClient(token).profile()["emailAddress"]
    _connect(db, user_id=user_id, email=email, token=token)
    return HTMLResponse(f"""<!doctype html><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<body style='font-family:-apple-system,sans-serif;background:#08070E;color:#F4F2FA;
display:flex;flex-direction:column;align-items:center;justify-content:center;
height:100vh;margin:0;gap:12px'>
<div style='font-size:40px'>&#10003;</div>
<div style='font-size:20px'>{email} connected</div>
<div style='color:#8A87A3;font-size:14px'>Returning to Super App&hellip;</div>
<a href='superapp://gmail-connected' style='color:#C7B8FF'>Open the app</a>
<script>setTimeout(function() {{ location.href = 'superapp://gmail-connected'; }}, 600);</script>
</body>""")


@router.post("/inbox/sync")
def sync_now(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    return run_think(db, agent="inbox", user_id=user_id, trigger={"kind": "email_sync"})


@router.post("/gmail/webhook/{token}")
async def gmail_webhook(token: str, request: Request, background: BackgroundTasks,
                        db: Session = Depends(get_db)):
    """Pub/Sub push: mail triaged seconds after it arrives."""
    settings = get_settings()
    if token != settings.gmail_webhook_token:
        raise HTTPException(status_code=403, detail="Bad webhook token")
    payload = await request.json()  # {message: {data: b64 {emailAddress, historyId}}}
    email = None
    try:
        email = json.loads(base64.b64decode(payload["message"]["data"]))["emailAddress"]
    except (KeyError, TypeError, ValueError):
        pass
    from ..models import GmailAccount
    from sqlalchemy import select

    if email:
        accts = list(db.scalars(select(GmailAccount).where(GmailAccount.email == email)))
    else:  # undecodable envelope: sync every connected user rather than miss mail
        accts = list(db.scalars(select(GmailAccount)))
    # Ack Pub/Sub immediately; triage continues in the background (Google
    # retries un-acked pushes, which would double-trigger slow syncs).
    from ..routers.screen import _background_think

    for user in {a.user_id for a in accts}:
        background.add_task(_background_think, "inbox", user,
                            {"kind": "email_sync", "reason": "pubsub"})
    return {"ok": True}


class DraftEdit(BaseModel):
    body: str = Field(min_length=1, max_length=8000)


@router.put("/inbox/drafts/{draft_id}")
def edit_draft(draft_id: str, body: DraftEdit, user_id: str = Depends(current_user_id),
               db: Session = Depends(get_db)):
    draft = get_draft(db, user_id=user_id, draft_id=draft_id)
    if draft.status == "sent":
        raise HTTPException(status_code=409, detail="Already sent")
    # The edit diff is the voice-learning signal (roadmap §Phase 3).
    append_event(db, user_id=user_id, type="draft_edited", agent="inbox", domain="inbox",
                 payload={"draft_id": draft.id, "before": draft.body[:2000], "after": body.body[:2000]})
    draft.body = body.body
    draft.status = "edited"
    db.commit()
    return {"ok": True}


@router.post("/inbox/drafts/{draft_id}/send")
def send_draft(draft_id: str, user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    """The user's tap IS the approval. Gated by the trust ladder."""
    settings = get_settings()
    if settings.gmail_scope_tier not in ("send", "modify"):
        raise HTTPException(status_code=403,
                            detail="Sending is off (gmail_scope_tier=read). Climb the trust ladder first.")
    draft = get_draft(db, user_id=user_id, draft_id=draft_id)
    if draft.status == "sent":
        raise HTTPException(status_code=409, detail="Already sent")
    was_edited = draft.status == "edited"
    msg = db.get(InboxMessage, draft.message_id)
    token = get_token(db, user_id=user_id, provider=f"gmail:{msg.account_email}")
    client = GmailClient(json.loads(token) if token else None)
    sent_id = client.send_reply(to_addr=msg.from_addr, subject=msg.subject,
                                body=draft.body, thread_id=msg.thread_id)
    from ..kernel import record_decision
    from ..models import utcnow
    draft.status = "sent"
    draft.sent_at = utcnow()
    msg.settled = True
    append_event(db, user_id=user_id, type="draft_sent", agent="inbox", domain="inbox",
                 payload={"draft_id": draft.id, "gmail_sent_id": sent_id, "edited": was_edited})
    # The tap is a typed verdict: sent-as-written is the kernel's cleanest signal.
    record_decision(db, user_id=user_id, agent="inbox", action_key="inbox.send_reply",
                    decided_by="user", verdict="edited" if was_edited else "accepted",
                    payload={"draft_id": draft.id})
    from ..memory import remember
    remember(db, user_id=user_id, domain="inbox", kind="sent", ref_id=draft.id,
             content=f"Nano replied to {msg.from_name} ({msg.from_addr}) — "
                     f"{msg.subject}: {draft.body[:600]}")
    db.commit()
    return render_screen(db, agent="inbox", user_id=user_id).model_dump()


class DeferBody(BaseModel):
    # Minutes to ADD to local time to get UTC (JS getTimezoneOffset convention).
    tz_offset_minutes: int = 0


@router.post("/inbox/drafts/{draft_id}/now")
def undefer_draft(draft_id: str, user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    """'Answer now' on a deferred card — bring the ask back immediately."""
    draft = get_draft(db, user_id=user_id, draft_id=draft_id)
    draft.defer_until = None
    append_event(db, user_id=user_id, type="draft_undeferred", agent="inbox", domain="inbox",
                 payload={"draft_id": draft.id})
    db.commit()
    return render_screen(db, agent="inbox", user_id=user_id).model_dump()


@router.post("/inbox/drafts/{draft_id}/defer")
def defer_draft(draft_id: str, body: DeferBody | None = None,
                user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    """'Ask me at 6pm' — hides the ask until 18:00 in the USER'S timezone."""
    from datetime import datetime, time, timedelta, timezone

    draft = get_draft(db, user_id=user_id, draft_id=draft_id)
    offset = timedelta(minutes=(body.tz_offset_minutes if body else 0))
    now_local = datetime.now(timezone.utc) - offset
    six_pm_local = datetime.combine(now_local.date(), time(18, 0))
    if six_pm_local <= now_local.replace(tzinfo=None):
        six_pm_local += timedelta(days=1)
    draft.defer_until = six_pm_local.replace(tzinfo=timezone.utc) + offset
    append_event(db, user_id=user_id, type="draft_deferred", agent="inbox", domain="inbox",
                 payload={"draft_id": draft.id})
    from ..kernel import record_decision
    record_decision(db, user_id=user_id, agent="inbox", action_key="inbox.send_reply",
                    decided_by="user", verdict="deferred", payload={"draft_id": draft.id})
    db.commit()
    return render_screen(db, agent="inbox", user_id=user_id).model_dump()
