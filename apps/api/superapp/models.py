"""Substrate tables (architecture §6). One Postgres instance; these are schemas, not services.

Phase 0 ships the cross-domain core: user_facts + events. Domain twins
(finance.transactions, nutrition.meals, ...) arrive with their verticals.
`user_id` is on every row from day one (architecture §8).
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserFact(Base):
    """Semantic memory — the heart of the app (architecture §6.2).

    One row per (user, domain, key). Superseded values are archived to `events`,
    never deleted, so beliefs stay traceable.
    """

    __tablename__ = "user_facts"
    __table_args__ = (
        UniqueConstraint("user_id", "domain", "key", name="uq_fact_identity"),
        Index("ix_facts_user_domain", "user_id", "domain"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    domain: Mapped[str] = mapped_column(String(32), nullable=False)  # finance | nutrition | wardrobe | inbox | goals | ...
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[dict] = mapped_column(JSON, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.7)  # 0..1; inferred-once facts are not gospel
    source_agent: Mapped[str] = mapped_column(String(32), nullable=False)
    source_run_id: Mapped[str | None] = mapped_column(String(36))  # provenance: which run learned this
    learned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # decay hygiene


class Event(Base):
    """Append-only episodic log (architecture §6.2): ingests, agent runs, user reactions."""

    __tablename__ = "events"
    __table_args__ = (Index("ix_events_user_time", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. agent_run, fact_superseded, insight_dismissed, llm_call
    agent: Mapped[str | None] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TokenVaultEntry(Base):
    """Encrypted-at-rest OAuth credentials (Plaid, Gmail). Most sensitive table in the app.

    Phase 0 defines the shape; encryption + first real tokens land with Phase 2 (Plaid).
    """

    __tablename__ = "token_vault"
    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_vault_identity"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)  # plaid | gmail
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
