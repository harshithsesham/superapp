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
    """Append-only episodic log (architecture §6.2): ingests, agent runs, user reactions.

    `domain` mirrors the fact entitlement model: context slices only include events
    whose domain is in the agent's scope. NULL = cross-domain/system telemetry,
    visible to every agent.
    """

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_user_time", "user_id", "created_at"),
        Index("ix_events_user_domain", "user_id", "domain"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. agent_run, fact_superseded, insight_dismissed, llm_call
    agent: Mapped[str | None] = mapped_column(String(32))
    domain: Mapped[str | None] = mapped_column(String(32))  # NULL = system/cross-domain
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NutritionMeal(Base):
    """Domain twin (architecture §6.2): the nutrition vertical's raw records.

    Twins hold data; `user_facts` holds beliefs. A meal is a record, so it lives
    here — never as a fact (write_fact enforces this).
    """

    __tablename__ = "nutrition_meals"
    __table_args__ = (Index("ix_meals_user_time", "user_id", "logged_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    logged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # photo | text
    photo_id: Mapped[str | None] = mapped_column(String(80))  # file in media storage
    description: Mapped[str] = mapped_column(Text, default="")
    kcal: Mapped[int | None] = mapped_column()
    protein_g: Mapped[float | None] = mapped_column(Float)
    carbs_g: Mapped[float | None] = mapped_column(Float)
    fat_g: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)  # 0 until estimated
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
