"""Inbox domain twin operations — the only module touching the inbox tables."""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..inbox.gmail_client import clean_email_text as _clean
from ..models import Event, GmailAccount, InboxDraft, InboxMessage


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

    from_counts: dict[str, int] = {}
    for m in msgs:
        from_counts[m.from_addr] = from_counts.get(m.from_addr, 0) + 1

    def row(m: InboxMessage) -> dict:
        d = drafts.get(m.id)
        deferred = bool(d and d.defer_until and aware(d.defer_until) > now)
        return {
            "id": m.id, "from_name": m.from_name, "from_addr": m.from_addr,
            "subject": m.subject, "gist": m.gist, "why_now": m.why_now,
            "clear_reason": m.clear_reason, "tier": m.tier, "settled": m.settled,
            "kind": getattr(m, "note_kind", "") or "",
            "flagged": bool(getattr(m, "suspicious", False)),
            "received_at": aware(m.received_at).isoformat(),
            "prior_from_sender": from_counts.get(m.from_addr, 1) - 1,
            "body": _clean(m.body_text or "")[:2500],
            "draft": {"id": d.id, "body": d.body, "status": d.status, "deferred": deferred} if d else None,
        }

    from ..models import UserFact
    mutes_fact = db.scalar(select(UserFact).where(
        UserFact.user_id == user_id, UserFact.domain == "inbox",
        UserFact.key == "mutes"))
    mutes = mutes_fact.value if mutes_fact and mutes_fact.value else {}
    muted_kinds = {k.lower() for k in mutes.get("kinds", [])}
    muted_senders = {a.lower() for a in mutes.get("senders", [])}

    def muted(m) -> bool:
        return (m.from_addr.lower() in muted_senders
                or ((getattr(m, "note_kind", "") or "").lower() in muted_kinds
                    if getattr(m, "note_kind", "") else False))

    open_asks = [row(m) for m in msgs if m.tier == "needs_reply" and not m.settled]
    cleared = [m for m in msgs if m.tier in ("cleared", "receipt")]
    cleared_by_reason: dict[str, int] = {}
    for m in cleared:
        key = m.clear_reason or "other"
        cleared_by_reason[key] = cleared_by_reason.get(key, 0) + 1

    sent: list[dict] = []
    sent_drafts = db.scalars(
        select(InboxDraft).where(InboxDraft.user_id == user_id,
                                 InboxDraft.status == "sent")
        .order_by(InboxDraft.sent_at.desc()).limit(8))
    by_id = {m.id: m for m in msgs}
    for d in sent_drafts:
        m = by_id.get(d.message_id) or db.get(InboxMessage, d.message_id)
        if m is None:
            continue
        sent.append({
            "kind": "reply", "to_name": m.from_name, "to_addr": m.from_addr,
            "subject": m.subject, "body": d.body[:2500],
            "sent_at": aware(d.sent_at).isoformat() if d.sent_at else "",
        })
    new_sends = db.scalars(
        select(Event).where(Event.user_id == user_id, Event.type == "email_sent_new")
        .order_by(Event.created_at.desc()).limit(8))
    for e in new_sends:
        sent.append({
            "kind": "new", "to_name": e.payload.get("to", ""),
            "to_addr": e.payload.get("to", ""),
            "subject": e.payload.get("subject", ""),
            "body": e.payload.get("body", "")[:2500],
            "sent_at": aware(e.created_at).isoformat(),
        })
    sent.sort(key=lambda x: x["sent_at"], reverse=True)

    # Gmail-simple Primary: every synced message, newest first, bodies trimmed
    # so downstream prompts stay lean.
    primary = []
    for m in msgs[:25]:
        r = row(m)
        r["body"] = r["body"][:1500]
        primary.append(r)

    return {
        "connected": bool(accounts(db, user_id)),
        "needs_reply": open_asks,
        "primary": primary,
        "worth_knowing": [row(m) for m in msgs
                          if m.tier == "worth_knowing" and not m.settled
                          and not muted(m)][:8],
        "cleared_count": len(cleared) + sum(
            1 for m in msgs if m.tier == "worth_knowing" and not m.settled and muted(m)),
        "cleared_by_reason": cleared_by_reason,
        "receipts": [row(m) for m in msgs if m.tier == "receipt"][:10],
        "pending_count": sum(1 for m in msgs if m.tier == "pending"),
        "sent": sent[:10],
    }
