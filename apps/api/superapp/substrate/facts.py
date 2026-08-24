"""Semantic memory operations, with the two hygiene rules from architecture §6.2:

- Conflict resolution: a new value for (user, domain, key) supersedes the old one;
  the old value is archived to `events` (type=fact_superseded), never deleted.
- Decay: reads exclude expired facts; expires_at is set by writers where relevant.
"""
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import UserFact, utcnow
from .events import append_event


def write_fact(
    db: Session,
    *,
    user_id: str,
    domain: str,
    key: str,
    value: dict,
    confidence: float = 0.7,
    source_agent: str,
    source_run_id: str | None = None,
    expires_at: datetime | None = None,
) -> UserFact:
    existing = db.scalar(
        select(UserFact).where(
            UserFact.user_id == user_id, UserFact.domain == domain, UserFact.key == key
        )
    )
    if existing is None:
        fact = UserFact(
            user_id=user_id,
            domain=domain,
            key=key,
            value=value,
            confidence=confidence,
            source_agent=source_agent,
            source_run_id=source_run_id,
            expires_at=expires_at,
        )
        db.add(fact)
        db.flush()
        return fact

    # Newer wins; archive the old belief so we can trace why the app believed it.
    append_event(
        db,
        user_id=user_id,
        type="fact_superseded",
        agent=source_agent,
        payload={
            "domain": domain,
            "key": key,
            "old_value": existing.value,
            "old_confidence": existing.confidence,
            "old_source_agent": existing.source_agent,
            "old_learned_at": existing.learned_at.isoformat(),
        },
    )
    existing.value = value
    existing.confidence = confidence
    existing.source_agent = source_agent
    existing.source_run_id = source_run_id
    existing.learned_at = utcnow()
    existing.expires_at = expires_at
    db.flush()
    return existing


def read_facts(db: Session, *, user_id: str, domains: list[str] | None, limit: int) -> list[UserFact]:
    """Scoped, non-expired fact slice. domains=None means all domains (wildcard scope)."""
    now = datetime.now(timezone.utc)
    stmt = select(UserFact).where(
        UserFact.user_id == user_id,
        or_(UserFact.expires_at.is_(None), UserFact.expires_at > now),
    )
    if domains is not None:
        stmt = stmt.where(UserFact.domain.in_(domains))
    stmt = stmt.order_by(UserFact.confidence.desc(), UserFact.learned_at.desc()).limit(limit)
    return list(db.scalars(stmt))
