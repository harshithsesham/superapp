"""Inbox domain twin operations — the only module touching the inbox tables."""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..inbox.gmail_client import clean_email_text as _clean
from ..models import Event, GmailAccount, InboxDraft, InboxMessage


def upsert_account(db: Session, *, user_id: str, email: str,
                   provider: str = "gmail") -> GmailAccount:
    """The same address may exist on two providers, so both identify a row.
    An older row with no provider set is adopted rather than duplicated."""
    acct = db.scalar(select(GmailAccount).where(
        GmailAccount.user_id == user_id, GmailAccount.email == email,
        GmailAccount.provider == provider))
    if acct is None:
        acct = GmailAccount(user_id=user_id, email=email, provider=provider)
        db.add(acct)
        db.flush()
    return acct


def accounts(db: Session, user_id: str) -> list[GmailAccount]:
    """Ordered, because the caller derives each mailbox's colour and the
    PRIMARY badge from position. An unordered query silently recoloured a
    person's mailboxes whenever a new row appeared."""
    return list(db.scalars(select(GmailAccount)
                           .where(GmailAccount.user_id == user_id)
                           .order_by(GmailAccount.created_at, GmailAccount.id)))


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
        # Envelope; absent from the stub mailbox and from older callers, so
        # every one of these is optional and defaults to "we do not know".
        to_addrs=msg.get("to_addrs", ""), cc_addrs=msg.get("cc_addrs", ""),
        reply_to=msg.get("reply_to", ""), message_id_hdr=msg.get("message_id_hdr", ""),
        in_reply_to=msg.get("in_reply_to", ""), list_id=msg.get("list_id", ""),
        precedence=msg.get("precedence", ""),
        has_list_unsubscribe=bool(msg.get("has_list_unsubscribe", False)),
    )
    db.add(row)
    db.flush()
    return row


def create_draft(db: Session, *, user_id: str, message_id: str, body: str,
                 generation_status: str = "ready", generation_reason: str = "",
                 used_imported_context: bool = False) -> InboxDraft:
    """A draft the person wrote is `ready` by definition. One the model wrote
    carries whatever the drafter reported — and anything but `ready` can never
    send itself."""
    draft = InboxDraft(user_id=user_id, message_id=message_id, body=body,
                       generation_status=generation_status, generation_reason=generation_reason,
                       used_imported_context=used_imported_context)
    db.add(draft)
    db.flush()
    return draft


def auto_reply_blocked(msg) -> str | None:
    signals = msg.signals or {}
    if signals.get("recovered"):
        return "Recovered mail needs explicit review before sending."
    if signals.get("context_incomplete"):
        return "Related context was unavailable; review this draft before sending."
    return None


def draft_unsendable(draft) -> str | None:
    """Why this draft must not go out — None when it may. One rule for every
    send path (the sync gate, the arming, the deadline re-check, the rule-enable
    sweep, the tap, the spoken "send it"): words the model never finished, or
    that nobody wrote, never leave. A person's own edit marks a draft ready."""
    if getattr(draft, "used_imported_context", False):
        return ("this one quotes your own notes, so you read it before it goes")
    if getattr(draft, "generation_status", "ready") != "ready":
        why = draft.generation_reason or draft.generation_status
        return f"draft was never finished ({why})"
    if not (draft.body or "").strip():
        return "draft is empty"
    return None


def mark_written_by_user(draft, body: str) -> None:
    """A person replaced the words: whatever the model failed to do no longer
    matters, and the draft becomes sendable on their say-so."""
    draft.body = body
    if body.strip():
        draft.generation_status = "ready"
        draft.generation_reason = ""
        # Only this explicit user edit/review releases the private-context
        # hold. Without it, even replacing the draft left it unsendable forever.
        draft.used_imported_context = False


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
        auto_at = (aware(d.auto_send_at) if d and d.status == "auto_pending" and d.auto_send_at
                   else None)
        return {
            "id": m.id, "from_name": m.from_name, "from_addr": m.from_addr,
            "box": m.account_email,
            "subject": m.subject, "gist": m.gist, "why_now": m.why_now,
            "clear_reason": m.clear_reason, "tier": m.tier, "settled": m.settled,
            # Two questions, not one. The UI can show a high-importance card
            # that owes nobody a reply without pretending an ask exists.
            "importance": getattr(m, "importance", "normal") or "normal",
            "requires_reply": bool(getattr(m, "requires_reply", False)),
            "rule_promoted": bool(getattr(m, "rule_promoted", False)),
            "kind": getattr(m, "note_kind", "") or "",
            "flagged": bool(getattr(m, "suspicious", False)),
            "received_at": aware(m.received_at).isoformat(),
            "prior_from_sender": from_counts.get(m.from_addr, 1) - 1,
            "body": _clean(m.body_text or "")[:2500],
            "draft": {"id": d.id, "body": d.body, "status": d.status, "deferred": deferred,
                      "generation": d.generation_status, "generation_reason": d.generation_reason,
                      "auto_send_at": auto_at.isoformat() if auto_at else None,
                      "sending_in": max(0, int((auto_at - now).total_seconds())) if auto_at else None,
                      } if d else None,
        }

    mutes = rules_fact(db, user_id, "mutes")
    prio = rules_fact(db, user_id, "priority")

    def muted(m) -> bool:
        # "Never from this sender" covers Needs you too, and a domain rule
        # covers every address on it. A standing "never miss" rule for the
        # same mail wins: the promise to surface beats the wish to hide.
        kind = getattr(m, "note_kind", "") or ""
        return (rule_matches(mutes, m.from_addr, kind)
                and not rule_matches(prio, m.from_addr, kind))

    def watched(m) -> bool:
        """Surfaced because the user asked never to miss it. It sits in Needs
        you — that is the promise — but it carries no draft, because a rule
        about what to SEE is not a claim that anyone is waiting on a reply."""
        return bool(getattr(m, "rule_promoted", False)) and m.tier != "needs_reply"

    open_asks = [row(m) for m in msgs
                 if (m.tier == "needs_reply" or watched(m))
                 and not m.settled and not muted(m)]
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

    _BOX_COLORS = ["#818CF8", "#7CF7C4", "#FF9DA8", "#FFD9A0", "#C7B8FF", "#5E7CFF"]
    _accts = accounts(db, user_id)
    mailboxes = [{
        "email": a.email,
        "primary": i == 0,
        "color": _BOX_COLORS[i % len(_BOX_COLORS)],
        "count": sum(1 for m in msgs if m.account_email == a.email),
        "recovering": bool(a.recovery_state),
        "sync_error": a.sync_error,
        "last_sync_at": a.last_sync_at.isoformat() if a.last_sync_at else None,
        "recovered_count": (a.recovery_state or {}).get("processed", 0),
        # So the app can name the mailbox honestly instead of assuming Gmail,
        # and send a reconnect back to the provider it belongs to.
        "provider": getattr(a, "provider", "") or "gmail",
    } for i, a in enumerate(_accts)]
    return {
        "connected": bool(_accts),
        "sync_incomplete": any(a.recovery_state or a.sync_error for a in _accts),
        "mailboxes": mailboxes,
        "needs_reply": open_asks,
        "primary": primary,
        # Watched mail is already up in Needs you; listing it twice reads as
        # two emails.
        "worth_knowing": [row(m) for m in msgs
                          if m.tier == "worth_knowing" and not m.settled
                          and not muted(m) and not watched(m)][:8],
        "cleared_count": len(cleared) + sum(
            1 for m in msgs
            if m.tier in ("worth_knowing", "needs_reply") and not m.settled and muted(m)),
        "cleared_by_reason": cleared_by_reason,
        "receipts": [row(m) for m in msgs if m.tier == "receipt"][:10],
        "pending_count": sum(1 for m in msgs if m.tier == "pending"),
        "sent": sent[:10],
    }


def rules_fact(db, user_id: str, key: str) -> dict:
    """A standing sender/kind rule set ("mutes" or "priority") as lowercase
    sets. Accepts the legacy list shape and the name->true map shape."""
    from ..models import UserFact
    fact = db.scalar(select(UserFact).where(
        UserFact.user_id == user_id, UserFact.domain == "inbox", UserFact.key == key))
    value = fact.value if fact and fact.value else {}
    return {"senders": {str(x).lower().strip() for x in (value.get("senders") or {})},
            "kinds": {str(x).lower().strip() for x in (value.get("kinds") or {})}}


def sender_matches(rules: set, from_addr: str) -> bool:
    """An exact address matches itself. A rule with no local part
    ("amazon.com", "@amazon.com") matches every address on that domain and
    its subdomains, never a look-alike ("amazon.com.evil.co")."""
    addr = (from_addr or "").lower().strip()
    domain = addr.rsplit("@", 1)[-1] if "@" in addr else ""
    for rule in rules:
        r = rule.lstrip("@").strip()
        if not r:
            continue
        if "@" in r:
            if addr == r:
                return True
        elif domain and (domain == r or domain.endswith("." + r)):
            return True
    return False


def rule_matches(rules: dict, from_addr: str, kind: str) -> bool:
    k = (kind or "").lower().strip()
    return sender_matches(rules["senders"], from_addr) or bool(k and k in rules["kinds"])


def replies_sent_in_thread(db, *, user_id: str, thread_id: str, hours: int = 24) -> int:
    """How many replies went out on this thread recently. The auto-reply loop
    backstop: two assistants answering each other stop after two rounds, and
    a thread the person is actively working stays theirs."""
    from datetime import timedelta

    from sqlalchemy import func

    from ..models import InboxDraft, InboxMessage, utcnow
    if not thread_id:
        return 0
    cutoff = utcnow() - timedelta(hours=hours)
    return db.scalar(
        select(func.count()).select_from(InboxDraft)
        .join(InboxMessage, InboxMessage.id == InboxDraft.message_id)
        .where(InboxDraft.user_id == user_id, InboxDraft.status == "sent",
               InboxDraft.sent_at >= cutoff, InboxMessage.thread_id == thread_id)) or 0


AUTO_REPLIES_PER_THREAD = 2
