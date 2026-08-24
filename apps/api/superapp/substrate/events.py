"""Append-only event log operations.

Events carry an optional `domain` so context slices can be scoped the same way
facts are (architecture §6.2). domain=None marks system/cross-domain telemetry
that every agent may see.
"""
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import Event


def append_event(
    db: Session,
    *,
    user_id: str,
    type: str,
    agent: str | None = None,
    domain: str | None = None,
    payload: dict | None = None,
) -> Event:
    event = Event(user_id=user_id, type=type, agent=agent, domain=domain, payload=payload or {})
    db.add(event)
    db.flush()
    return event


def recent_events(
    db: Session,
    *,
    user_id: str,
    limit: int,
    types: list[str] | None = None,
    exclude_types: list[str] | None = None,
    domains: list[str] | None = None,
) -> list[Event]:
    """domains=None means all domains (wildcard scope); a list restricts to those
    domains plus domain-less (system) events."""
    stmt = select(Event).where(Event.user_id == user_id)
    if types:
        stmt = stmt.where(Event.type.in_(types))
    if exclude_types:
        stmt = stmt.where(Event.type.not_in(exclude_types))
    if domains is not None:
        stmt = stmt.where(or_(Event.domain.is_(None), Event.domain.in_(domains)))
    stmt = stmt.order_by(Event.created_at.desc(), Event.id.desc()).limit(limit)
    return list(db.scalars(stmt))
