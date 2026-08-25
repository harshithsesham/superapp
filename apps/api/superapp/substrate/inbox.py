"""Inbox domain twin operations — the only module touching the inbox tables."""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import GmailAccount, InboxDraft, InboxMessage


def upsert_account(db: Session, *, user_id: str, email: str) -> GmailAccount:
    acct = db.scalar(select(GmailAccount).where(
        GmailAccount.user_id == user_id, GmailAccount.email == email))
    if acct is None:
        acct = GmailAccount(user_id=user_id, email=email)
        db.add(acct)
        db.flush()
    return acct


def accounts(db: Session, user_id: str) -> list[GmailAccount]:
    return list(db.scalars(select(GmailAccount).where(GmailAccount.user_id == user_id)))


def insert_message(db: Session, *, user_id: str, account_email: str, msg: dict) -> InboxMessage | None:
    """Returns None if the message is already known (idempotent sync)."""
    existing = db.scalar(select(InboxMessage).where(
        InboxMessage.user_id == user_id, InboxMessage.gmail_msg_id == msg["gmail_msg_id"]))
    if existing is not None:
        return None
    row = InboxMessage(
        user_id=user_id, account_email=account_email,
        gmail_msg_id=msg["gmail_msg_id"], thread_id=msg["thread_id"],
        from_name=msg["from_name"], from_addr=msg["from_addr"], subject=msg["subject"],
        body_text=msg["body_text"],
        received_at=datetime.fromisoformat(msg["received_at"]),
    )
    db.add(row)
    db.flush()
    return row


def create_draft(db: Session, *, user_id: str, message_id: str, body: str) -> InboxDraft:
    draft = InboxDraft(user_id=user_id, message_id=message_id, body=body)
    db.add(draft)
    db.flush()
    return draft


def get_draft(db: Session, *, user_id: str, draft_id: str) -> InboxDraft:
    draft = db.get(InboxDraft, draft_id)
    if draft is None or draft.user_id != user_id:
        raise ValueError(f"No draft {draft_id!r} for user")
    return draft


def inbox_context(db: Session, user_id: str) -> dict:
    """The inbox slice of ContextSlice.domain_data."""
    now = datetime.now(timezone.utc)
    msgs = list(db.scalars(
        select(InboxMessage).where(InboxMessage.user_id == user_id)
        .order_by(InboxMessage.received_at.desc()).limit(200)
    ))
    drafts = {d.message_id: d for d in db.scalars(
        select(InboxDraft).where(InboxDraft.user_id == user_id, InboxDraft.status != "dismissed"))}

    def aware(dt):
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    def row(m: InboxMessage) -> dict:
        d = drafts.get(m.id)
        deferred = bool(d and d.defer_until and aware(d.defer_until) > now)
        return {
            "id": m.id, "from_name": m.from_name, "from_addr": m.from_addr,
            "subject": m.subject, "gist": m.gist, "why_now": m.why_now,
            "clear_reason": m.clear_reason, "tier": m.tier, "settled": m.settled,
            "received_at": aware(m.received_at).isoformat(),
            "draft": {"id": d.id, "body": d.body, "status": d.status, "deferred": deferred} if d else None,
        }

    open_asks = [row(m) for m in msgs if m.tier == "needs_reply" and not m.settled]
    cleared = [m for m in msgs if m.tier in ("cleared", "receipt")]
    cleared_by_reason: dict[str, int] = {}
    for m in cleared:
        key = m.clear_reason or "other"
        cleared_by_reason[key] = cleared_by_reason.get(key, 0) + 1

    return {
        "connected": bool(accounts(db, user_id)),
        "needs_reply": open_asks,
        "worth_knowing": [row(m) for m in msgs if m.tier == "worth_knowing" and not m.settled][:8],
        "cleared_count": len(cleared),
        "cleared_by_reason": cleared_by_reason,
        "receipts": [row(m) for m in msgs if m.tier == "receipt"][:10],
        "pending_count": sum(1 for m in msgs if m.tier == "pending"),
    }
