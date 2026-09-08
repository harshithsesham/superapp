"""The auto-reply grace window.

An auto-reply rule no longer sends on the spot. The draft sits in Needs you
marked `auto_pending` with a deadline, the lock screen shows the stepped
Live Activity counting down, and the person has that window to edit it (the
edited words go out), send it now, or say "I'll send it myself". Do nothing
and it sends. Every gate is re-checked at the deadline against the body as
it is THEN, so an edit that introduced a blank or a new address holds it.

Timing: an in-process timer fires at the deadline (uvicorn runs one process;
syncs and their timers live in it). Two backstops catch a restart inside
the window: every inbox sync and every dispatcher tick send whatever is due.
"""
import threading
from datetime import timedelta

from sqlalchemy import event, select, update
from sqlalchemy.orm import Session

from .config import get_settings
from .models import InboxDraft, InboxMessage, utcnow

AR_STEPS = ["Reading", "Drafting", "Sending", "Sent"]
TIMERS_ENABLED = True   # tests switch this off; the backstops still run
STALE_CLAIM_MINUTES = 5   # an auto_sending row older than this was a crash mid-send
_REARM_TRIES = 6           # _fire re-checks for a row the sync has not committed yet


def _aware(dt):
    """SQLite hands back naive UTC; Postgres hands back aware. Compare as aware."""
    from datetime import timezone
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _la(db: Session, user_id: str, event: str, state: dict, **kw) -> bool:
    from .push import live_activity
    try:
        return live_activity(db, user_id=user_id, event=event, state=state, **kw)
    except Exception:  # noqa: BLE001 — the lock screen is never load-bearing
        return False


def schedule(db: Session, *, draft: InboxDraft, msg: InboxMessage, gate_tier: int) -> int:
    """Arm the window for a draft that passed every auto-send gate. Returns
    the window length in seconds. The row is only marked here; the lock-screen
    activity, the fallback push and the timer all wait for the caller's
    commit, so a sync that rolls back never announces a window that does not
    exist and a timer never fires into an uncommitted row."""
    from .substrate.inbox import auto_reply_blocked, draft_unsendable
    why = draft_unsendable(draft) or auto_reply_blocked(msg)
    if why:
        raise ValueError(f"only a finished draft can be scheduled to send itself: {why}")
    from .substrate import append_event
    delay = max(0, int(get_settings().auto_reply_delay_seconds))
    draft.status = "auto_pending"
    draft.auto_send_at = utcnow() + timedelta(seconds=delay)
    append_event(db, user_id=draft.user_id, type="draft_auto_scheduled", agent="inbox",
                 domain="inbox", payload={"draft_id": draft.id, "kind": msg.note_kind,
                                          "in_seconds": delay, "risk_tier": gate_tier})
    did, uid = draft.id, draft.user_id
    from_name, body, deadline = msg.from_name, draft.body[:140], _aware(draft.auto_send_at)

    def _announce_and_arm(_session=None) -> None:
        announce(uid, from_name=from_name, body=body, deadline=deadline)
        arm(did, deadline)

    if db.in_transaction():
        event.listen(db, "after_commit", _announce_and_arm, once=True)
    else:
        _announce_and_arm()
    return delay


def announce(user_id: str, *, from_name: str, body: str, deadline) -> None:
    """Lock-screen activity (with its alert) or, without a token, a push.
    Runs on its own session, after the scheduling transaction committed."""
    from .db import SessionLocal
    db = SessionLocal()
    try:
        left = max(0, int((deadline - utcnow()).total_seconds()))
        label = f"Sending to {from_name} in {left}s"
        shown = _la(db, user_id, "start",
                    {"status": label, "stage": label, "steps": AR_STEPS, "stepIndex": 2,
                     "quoteLabel": "WHAT'S GOING OUT", "quote": body,
                     "deadline": deadline.timestamp()},
                    title="Auto-reply", alert_title=f"Replying to {from_name} in {left}s",
                    alert_body="Open Inbox Zero to edit it, send it now, or take it over.")
        if not shown:
            from .push import send_push
            try:
                send_push(db, user_id=user_id, agent="inbox",
                          title=f"Replying to {from_name} in {left}s",
                          body=f"\u201c{body[:90]}\u201d  Open Inbox Zero to edit, send now, or take over.")
            except Exception:  # noqa: BLE001
                pass
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    finally:
        db.close()


def arm(draft_id: str, deadline, tries_left: int = _REARM_TRIES) -> None:
    """Fire at the deadline (computed now, so a long sync still fires on time)."""
    if not TIMERS_ENABLED:
        return
    left = max(0.0, (deadline - utcnow()).total_seconds()) + 1
    t = threading.Timer(left, _fire, args=(draft_id, tries_left))
    t.daemon = True
    t.start()


def rearm_all() -> int:
    """On process start: every open window gets its timer back."""
    from .db import SessionLocal
    db = SessionLocal()
    try:
        rows = list(db.scalars(select(InboxDraft).where(InboxDraft.status == "auto_pending")))
        for d in rows:
            if d.auto_send_at is not None:
                arm(d.id, _aware(d.auto_send_at))
        return len(rows)
    finally:
        db.close()


def refresh_activity(db: Session, draft: InboxDraft, msg: InboxMessage) -> None:
    """After an in-window edit: the lock screen shows the words that will go out."""
    if draft.status != "auto_pending" or draft.auto_send_at is None:
        return
    deadline = _aware(draft.auto_send_at)
    left = max(0, int((deadline - utcnow()).total_seconds()))
    label = f"Sending to {msg.from_name} in {left}s"
    _la(db, draft.user_id, "update",
        {"status": label, "stage": label, "steps": AR_STEPS, "stepIndex": 2,
         "quoteLabel": "WHAT'S GOING OUT", "quote": draft.body[:140],
         "deadline": deadline.timestamp()})


def end_activity_sent(db: Session, user_id: str, from_name: str) -> None:
    _la(db, user_id, "end", {"status": "Sent.", "stage": f"Sent to {from_name}.",
                             "steps": AR_STEPS, "stepIndex": 3})


def claim(db: Session, draft_id: str, *, stale_before=None) -> bool:
    """Atomically take a draft for sending. Exactly one of any number of
    concurrent callers (timer, sync backstop, dispatcher, 'Send now') wins;
    the claim is committed before any network call so a later rollback in the
    caller's transaction cannot resurrect it. With `stale_before`, reclaim an
    auto_sending row whose claim is older than that instant (a crash mid-send);
    the fresh claimed_at makes that exclusive too."""
    now = utcnow()
    if stale_before is None:
        cond = (InboxDraft.status == "auto_pending",)
    else:
        cond = (InboxDraft.status == "auto_sending", InboxDraft.claimed_at <= stale_before)
    n = db.execute(update(InboxDraft)
                   .where(InboxDraft.id == draft_id, *cond)
                   .values(status="auto_sending", claimed_at=now)).rowcount
    db.commit()
    return n == 1


def end_activity_held(db: Session, user_id: str) -> None:
    _la(db, user_id, "end", {"status": "Held for you.", "stage": "Held for you.",
                             "steps": AR_STEPS, "stepIndex": 2})


def _fire(draft_id: str, tries_left: int = _REARM_TRIES) -> None:
    from .db import SessionLocal
    db = SessionLocal()
    try:
        if send_due(db, draft_id=draft_id) == 0:
            d = db.get(InboxDraft, draft_id)
            if d is None and tries_left > 0:
                # Not committed yet (a long sync): look again shortly.
                arm(draft_id, utcnow() + timedelta(seconds=5), tries_left - 1)
            elif d is not None and d.status == "auto_pending" and d.auto_send_at is not None \
                    and _aware(d.auto_send_at) > utcnow() and tries_left > 0:
                arm(draft_id, _aware(d.auto_send_at), tries_left - 1)   # deadline moved
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    finally:
        db.close()


def cancel(db: Session, draft: InboxDraft, *, reason: str, by_user: bool = True) -> bool:
    """Take the draft out of the window. Returns True if it was pending."""
    if draft.status != "auto_pending":
        return False   # not in a window, or already claimed for sending (too late)
    from .kernel import record_decision
    from .substrate import append_event
    draft.status = "waiting"
    draft.auto_send_at = None
    append_event(db, user_id=draft.user_id, type="draft_auto_cancelled", agent="inbox",
                 domain="inbox", payload={"draft_id": draft.id, "reason": reason})
    if by_user:
        # Stopping an auto-reply is a verdict on the rule: it counts against
        # the earned autonomy, the way an undo does.
        record_decision(db, user_id=draft.user_id, agent="inbox", action_key="inbox.auto_reply",
                        decided_by="user", verdict="rejected",
                        payload={"draft_id": draft.id, "reason": reason})
    _la(db, draft.user_id, "end",
        {"status": "Held for you.", "stage": "Held for you.", "steps": AR_STEPS, "stepIndex": 2})
    return True


def send_due(db: Session, *, draft_id: str | None = None, user_id: str | None = None) -> int:
    """Send every auto_pending draft whose deadline has passed (optionally one
    draft, or one user's). Each is re-gated on its current body first."""
    now = utcnow()
    q = select(InboxDraft).where(InboxDraft.status == "auto_pending", InboxDraft.auto_send_at <= now)
    if draft_id:
        q = q.where(InboxDraft.id == draft_id)
    if user_id:
        q = q.where(InboxDraft.user_id == user_id)
    sent = 0
    for d in list(db.scalars(q)):
        if not claim(db, d.id):
            continue   # someone else has it
        d.status = "auto_sending"   # the session does not refresh after commit
        if _send_one(db, d):
            sent += 1
        db.commit()   # sent or held: durable regardless of the caller's transaction

    # A crash between claim and send leaves an auto_sending row. Its first
    # attempt may already have reached Gmail, so it is never re-sent: it goes
    # back to the person as an ordinary ask, flagged, once the claim is stale.
    cutoff = now - timedelta(minutes=STALE_CLAIM_MINUTES)
    stale = select(InboxDraft).where(InboxDraft.status == "auto_sending",
                                     InboxDraft.claimed_at <= cutoff)
    if draft_id:
        stale = stale.where(InboxDraft.id == draft_id)
    if user_id:
        stale = stale.where(InboxDraft.user_id == user_id)
    for d in list(db.scalars(stale)):
        if not claim(db, d.id, stale_before=cutoff):
            continue
        d.status = "auto_sending"
        from .substrate import append_event
        append_event(db, user_id=d.user_id, type="draft_auto_uncertain", agent="inbox",
                     domain="inbox", payload={"draft_id": d.id,
                                              "why": "a send was interrupted; check the thread"})
        _hold(db, d, db.get(InboxMessage, d.message_id), "send did not confirm; check the thread")
        db.commit()
    return sent


def _hold(db: Session, d: InboxDraft, msg: InboxMessage | None, why: str) -> None:
    from .substrate import append_event
    d.status = "waiting"
    d.auto_send_at = None
    d.claimed_at = None
    append_event(db, user_id=d.user_id, type="draft_auto_held", agent="inbox", domain="inbox",
                 payload={"draft_id": d.id, "why": why})
    _la(db, d.user_id, "end",
        {"status": "Held for you.", "stage": "Held for you.", "steps": AR_STEPS, "stepIndex": 2})


def _send_one(db: Session, d: InboxDraft) -> bool:
    from .kernel import record_decision
    from .policy import assess, draft_leaks_new_destination, has_placeholder
    from .substrate import append_event
    from .substrate.inbox import AUTO_REPLIES_PER_THREAD, replies_sent_in_thread

    settings = get_settings()
    msg = db.get(InboxMessage, d.message_id)
    if msg is None or msg.settled:
        _hold(db, d, msg, "message gone or settled")
        return False
    if settings.gmail_scope_tier not in ("send", "modify"):
        _hold(db, d, msg, "sending is off")
        return False
    from .substrate.inbox import auto_reply_blocked, draft_unsendable
    why = draft_unsendable(d) or auto_reply_blocked(msg)
    if why:
        _hold(db, d, msg, why)   # legacy rows, or a status changed after arming
        return False
    gate = assess("inbox.auto_reply", provenance="email", suspicious=bool(msg.suspicious))
    if not gate.allowed:
        _hold(db, d, msg, gate.reason)
        return False
    if has_placeholder(d.body):
        _hold(db, d, msg, "draft still has a blank")
        return False
    if draft_leaks_new_destination(d.body, msg.body_text or "",
                                   allowed=f"{msg.from_addr} {msg.account_email}"):
        _hold(db, d, msg, "draft carries a new destination")
        return False
    if replies_sent_in_thread(db, user_id=d.user_id, thread_id=msg.thread_id) >= AUTO_REPLIES_PER_THREAD:
        _hold(db, d, msg, "thread already had its auto-replies today")
        return False
    from .inbox.factory import send_via
    try:
        sent_id = send_via(db, d.user_id, msg, d.body, auto=True)
    except Exception as e:  # noqa: BLE001
        _hold(db, d, msg, f"send failed: {type(e).__name__}")
        return False
    d.status = "sent"
    d.sent_at = utcnow()
    d.auto_send_at = None
    d.claimed_at = None
    msg.tier = "worth_knowing"
    append_event(db, user_id=d.user_id, type="draft_sent", agent="inbox", domain="inbox",
                 payload={"draft_id": d.id, "gmail_sent_id": sent_id, "auto": True,
                          "kind": msg.note_kind, "window": True})
    record_decision(db, user_id=d.user_id, agent="inbox", action_key="inbox.auto_reply",
                    decided_by="nano", verdict="acted",
                    payload={"draft_id": d.id, "kind": msg.note_kind, "risk_tier": gate.tier,
                             "provenance": "email", "window_seconds": settings.auto_reply_delay_seconds})
    try:
        from .llm.provider import LLMProvider
        from .memory import remember
        from .people import update_person
        update_person(db, LLMProvider(), d.user_id, email=msg.from_addr, name=msg.from_name,
                      direction="user_wrote", subject=msg.subject, body=d.body[:4000])
        remember(db, user_id=d.user_id, domain="inbox", kind="sent", ref_id=d.id,
                 content=f"Nano auto-replied to {msg.from_name} ({msg.from_addr}) — "
                         f"{msg.subject}: {d.body[:600]}")
    except Exception:  # noqa: BLE001 — memory is best-effort
        pass
    _la(db, d.user_id, "end",
        {"status": "Sent.", "stage": f"Sent to {msg.from_name}.", "steps": AR_STEPS, "stepIndex": 3})
    return True
