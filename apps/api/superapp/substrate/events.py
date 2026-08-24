"""Append-only event log operations."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Event


def append_event(
    db: Session,
    *,
    user_id: str,
    type: str,
    agent: str | None = None,
    payload: dict | None = None,
) -> Event:
    event = Event(user_id=user_id, type=type, agent=agent, payload=payload or {})
    db.add(event)
    db.flush()
    return event


def recent_events(db: Session, *, user_id: str, limit: int, types: list[str] | None = None) -> list[Event]:
    stmt = select(Event).where(Event.user_id == user_id)
    if types:
        stmt = stmt.where(Event.type.in_(types))
    stmt = stmt.order_by(Event.created_at.desc(), Event.id.desc()).limit(limit)
    return list(db.scalars(stmt))
