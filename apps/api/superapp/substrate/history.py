"""The conversation record: past mail, kept for context and never for action.

Two tables, two jobs. `inbox_messages` is a queue — everything in it is
triaged, drafted for, possibly archived. `mail_history` is a record. Nothing
downstream reads it looking for work, which is the whole reason a deep import
is safe to offer: importing two years of mail cannot produce two years of
replies, because the reply path does not look here.

What the record buys, that headers alone never could:
  * "have I ever answered this person" — from the user's own SENT mail, which
    the queue filters out by design.
  * "what did we agree in March" — the thread, whole, at draft time.
  * who actually matters, dated by when things happened rather than by when
    the import ran.
"""
from datetime import datetime, timezone
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import memory
from ..models import MailHistory, utcnow

# How many of the imported messages get a model-backed profile update. The
# record is dense at the recent end and thin at the old end; spending one call
# per message across two years would cost more than the profiles are worth.
ENRICH_RECENT = 60


def _dt(value) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return utcnow()
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def record_message(db: Session, *, user_id: str, account_email: str,
                   msg: dict) -> MailHistory | None:
    """One historical message into the record. Idempotent per gmail id."""
    gid = msg.get("gmail_msg_id", "")
    if not gid:
        return None
    if db.scalar(select(MailHistory).where(MailHistory.user_id == user_id,
                                           MailHistory.gmail_msg_id == gid)) is not None:
        return None
    row = MailHistory(
        user_id=user_id, account_email=account_email, gmail_msg_id=gid,
        thread_id=msg.get("thread_id", ""),
        direction=msg.get("direction", "inbound"),
        from_addr=msg.get("from_addr", ""), to_addrs=msg.get("to_addrs", ""),
        subject=(msg.get("subject") or "")[:256],
        body_text=msg.get("body_text", ""),
        occurred_at=_dt(msg.get("received_at")),
    )
    db.add(row)
    db.flush()
    return row


def import_history(db: Session, *, user_id: str, account_email: str,
                   messages: list[dict], provider=None,
                   enrich_recent: int = ENRICH_RECENT, mail_provider: str = "gmail") -> dict:
    """Import past conversation. Records it, remembers it, and dates it.

    Explicitly does NOT: triage, draft, archive, send, or touch the queue. The
    only writes are mail_history rows, memory chunks, and people counters.

    Messages are processed newest-first so the model-backed profile enrichment
    is spent on the freshest exchanges, where the relationship actually is.
    """
    from ..people import update_person

    ordered = sorted(messages, key=lambda m: _dt(m.get("received_at")), reverse=True)
    stats = {"seen": len(ordered), "recorded": 0, "chunks": 0,
             "people": 0, "enriched": 0,
             "memory": "on" if memory.available(db) else "unavailable (needs Postgres)"}

    for msg in ordered:
        row = record_message(db, user_id=user_id, account_email=account_email, msg=msg)
        if row is None:
            continue
        stats["recorded"] += 1

        # Searchable, with the date it happened and a link back to the original.
        body = (row.body_text or "").strip()
        if body:
            who = ("you wrote to " + (row.to_addrs or "them")
                   if row.direction == "outbound" else f"{row.from_addr} wrote to you")
            stats["chunks"] += memory.remember(
                db, user_id=user_id, domain="inbox", kind="mail",
                ref_id=f"mail:{row.gmail_msg_id}",
                content=f"Subject: {row.subject}\n({who})\n\n{body}",
                source=mail_provider, author=row.from_addr, title=row.subject,
                source_ref=(f"https://outlook.office.com/mail/deeplink/read/{quote(row.gmail_msg_id, safe='')}"
                            if mail_provider == "outlook" else
                            f"https://mail.google.com/mail/u/0/#all/{row.gmail_msg_id}"),
                event_at=row.occurred_at)

        if provider is None:
            continue
        # The counterparty is the sender inbound, the recipients outbound.
        counterparties = ([row.from_addr] if row.direction == "inbound"
                          else [a for a in (row.to_addrs or "").split(",") if a][:3])
        enrich = stats["enriched"] < enrich_recent
        for addr in counterparties:
            person = update_person(
                db, provider, user_id, email=addr,
                direction=("from_them" if row.direction == "inbound" else "user_wrote"),
                subject=row.subject, body=body[:4000],
                occurred_at=row.occurred_at, enrich=enrich)
            if person is not None:
                stats["people"] += 1
                if enrich:
                    stats["enriched"] += 1
                    enrich = stats["enriched"] < enrich_recent
    db.flush()
    return stats


def sender_history(db: Session, *, user_id: str, addr: str) -> dict:
    """What the record knows about one correspondent. `replied_to_them` is the
    signal the queue could never produce: it comes from the user's own sent
    mail, which the working set filters out."""
    addr = (addr or "").lower().strip()
    if not addr:
        return {}
    # Addresses are stored as the sender spelled them, so compare folded:
    # "Priya@Eureka.io" and "priya@eureka.io" are one correspondent, and a
    # case-sensitive match would report a lifelong contact as a stranger.
    same_sender = func.lower(MailHistory.from_addr) == addr
    # Recipients live in one comma-joined column, so match the WHOLE address
    # between delimiters. An unanchored substring lets "s@x.com" inherit
    # "boss@x.com"'s history, and underscores are wildcards in LIKE.
    replied_to = ("," + func.lower(MailHistory.to_addrs) + ",").contains(
        f",{addr},", autoescape=True)
    inbound = db.scalar(select(func.count()).select_from(MailHistory).where(
        MailHistory.user_id == user_id, same_sender,
        MailHistory.direction == "inbound")) or 0
    outbound = db.scalar(select(func.count()).select_from(MailHistory).where(
        MailHistory.user_id == user_id, MailHistory.direction == "outbound",
        replied_to)) or 0
    first = db.scalar(select(func.min(MailHistory.occurred_at)).where(
        MailHistory.user_id == user_id, same_sender))
    last = db.scalar(select(func.max(MailHistory.occurred_at)).where(
        MailHistory.user_id == user_id, same_sender))
    return {
        "messages_from_them": inbound,
        "replied_to_them": outbound,
        "first_contact": first.isoformat() if first else "",
        "last_contact": last.isoformat() if last else "",
    }


def thread_history(db: Session, *, user_id: str, thread_id: str,
                   limit: int = 8, chars: int = 700) -> list[dict]:
    """The conversation so far, oldest first — what a person would scroll up to
    read before replying, and what the drafter never had."""
    if not thread_id:
        return []
    rows = db.scalars(select(MailHistory).where(
        MailHistory.user_id == user_id, MailHistory.thread_id == thread_id)
        .order_by(MailHistory.occurred_at.desc()).limit(limit)).all()
    return [{
        "who": "the user" if r.direction == "outbound" else (r.from_addr or "them"),
        "when": r.occurred_at.isoformat() if r.occurred_at else "",
        "subject": r.subject,
        "excerpt": (r.body_text or "")[:chars],
    } for r in reversed(rows)]
