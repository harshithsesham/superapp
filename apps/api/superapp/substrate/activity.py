"""The signal-fate view (Nano V4 "Context"): today's events rendered as
what-happened -> verdict. Only wildcard-scope agents (the Hub) receive it."""
from datetime import datetime, time, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Event, InboxMessage

# event/tier -> (verdict chip, tone)
_VERDICTS = {
    "needs_reply": ("BECAME A QUESTION", "ask"),
    "worth_knowing": ("FLAGGED TO READ", "did"),
    "receipt": ("LOGGED AND FILED", "filed"),
    "cleared": ("FILED", "filed"),
    "draft_created": ("REPLY DRAFTED", "did"),
    "meal_estimated": ("MEAL LOGGED", "did"),
    "transactions_synced": ("RECONCILED", "did"),
    "outfits_generated": ("LOOKS PREPARED", "did"),
    "draft_sent": ("SENT WITH YOUR YES", "did"),
    "style_distilled": ("LEARNED YOUR TASTE", "did"),
    "reply_style_distilled": ("LEARNED YOUR VOICE", "did"),
    "task_completed": ("SCOUTED THE WEB", "did"),
    "task_queued": ("ERRAND TAKEN", "did"),
}
_NOISE = ("llm_call", "screen_view", "agent_run", "inbox_synced", "push_sent")


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def activity_context(db: Session, user_id: str) -> dict:
    now = datetime.now(timezone.utc)
    day_start = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)

    events_today = db.scalar(
        select(func.count()).select_from(Event).where(
            Event.user_id == user_id, Event.created_at >= day_start,
            Event.type.not_in(_NOISE))
    ) or 0
    mail_today = db.scalar(
        select(func.count()).select_from(InboxMessage).where(
            InboxMessage.user_id == user_id, InboxMessage.created_at >= day_start)
    ) or 0
    signals_today = events_today + mail_today

    items = []
    # Mail signals get their tier verdicts (the richest source today).
    msgs = db.scalars(
        select(InboxMessage).where(InboxMessage.user_id == user_id,
                                   InboxMessage.created_at >= day_start)
        .order_by(InboxMessage.created_at.desc()).limit(12))
    for m in msgs:
        verdict, tone = _VERDICTS.get(m.tier, ("FILED", "filed"))
        items.append({
            "text": (m.gist or m.subject or m.from_name)[:90],
            "verdict": verdict, "tone": tone,
            "at": _aware(m.created_at), "sort": _aware(m.created_at),
        })
    # Non-mail moments.
    events = db.scalars(
        select(Event).where(Event.user_id == user_id, Event.created_at >= day_start,
                            Event.type.in_(list(_VERDICTS)))
        .order_by(Event.created_at.desc()).limit(8))
    for e in events:
        verdict, tone = _VERDICTS[e.type]
        label = {
            "meal_estimated": f"Logged a meal — {e.payload.get('kcal', '?')} kcal",
            "outfits_generated": f"Prepared {e.payload.get('count', '')} looks for today",
            "draft_sent": "A reply left with your yes",
            "task_completed": f"Scouted: {e.payload.get('summary', '')[:70]}",
            "task_queued": f"Errand: {e.payload.get('instruction', '')[:70]}",
            "transactions_synced": f"{e.payload.get('new', 0)} new transactions reconciled",
        }.get(e.type, e.type.replace("_", " ").capitalize())
        items.append({"text": label, "verdict": verdict, "tone": tone,
                      "at": _aware(e.created_at), "sort": _aware(e.created_at)})

    items.sort(key=lambda i: i["sort"], reverse=True)
    return {
        "signals_today": signals_today,
        "items": [{k: v for k, v in i.items() if k != "sort"} for i in items[:8]],
    }
