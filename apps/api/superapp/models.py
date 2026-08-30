"""Substrate tables (architecture §6). One Postgres instance; these are schemas, not services.

Phase 0 ships the cross-domain core: user_facts + events. Domain twins
(finance.transactions, nutrition.meals, ...) arrive with their verticals.
`user_id` is on every row from day one (architecture §8).
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Float, Index, Integer, String,
                        Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FlightWatch(Base):
    """The Flycatcher: a standing flight-price watch. The scout re-checks it
    daily; the person hears about it only on a new low or a hit target."""

    __tablename__ = "flight_watches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    target_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    best_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentTask(Base):
    """The scout's queue: web-research errands the person spoke into being.
    Results are structured shortlists; every state change is an event."""

    __tablename__ = "agent_tasks"
    __table_args__ = (
        Index("ix_tasks_user", "user_id", "created_at"),
        Index("ix_tasks_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), default="research")
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|done|failed
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    watch_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Decision(Base):
    """The decision ledger (north star step 3) — every judgment call, typed.

    Who decided (nano or the user), over which capability (action_key), and how
    it landed (accepted / edited / rejected / undone / deferred / acted). The
    permission kernel reads nothing else: autonomy is earned from these rows.
    """

    __tablename__ = "decisions"
    __table_args__ = (
        Index("ix_decisions_user_key", "user_id", "action_key", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    agent: Mapped[str] = mapped_column(String(32), nullable=False)
    action_key: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. inbox.send_reply
    decided_by: Mapped[str] = mapped_column(String(8), nullable=False)  # nano | user
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)  # accepted | edited | rejected | undone | deferred | acted
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AutonomyGrant(Base):
    """A promotion on the autonomy ladder — always evidence-carrying.

    level: 0 observe · 1 draft · 2 ask first · 3 act + report · 4 silent.
    Grants above the default level exist only with the user's explicit yes
    (granted_by="user"); one undo revokes (revoked_at set, reason kept).
    """

    __tablename__ = "autonomy_grants"
    __table_args__ = (
        Index("ix_grants_user_key", "user_id", "action_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action_key: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[int] = mapped_column(nullable=False)
    granted_by: Mapped[str] = mapped_column(String(8), default="user")  # user | system
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)  # counts at grant time
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoke_reason: Mapped[str] = mapped_column(String(128), default="")


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
    fiber_g: Mapped[float | None] = mapped_column(Float)
    sugar_g: Mapped[float | None] = mapped_column(Float)
    sodium_mg: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)  # 0 until estimated
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PlaidItem(Base):
    """One linked institution. The access token lives encrypted in token_vault
    (provider = "plaid:{item_id}"); this row holds the operational state."""

    __tablename__ = "plaid_items"
    __table_args__ = (UniqueConstraint("user_id", "item_id", name="uq_plaid_item"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    item_id: Mapped[str] = mapped_column(String(64), nullable=False)
    institution: Mapped[str] = mapped_column(String(128), default="")
    sync_cursor: Mapped[str] = mapped_column(Text, default="")  # transactions/sync cursor
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FinanceAccount(Base):
    """Domain twin: accounts at linked institutions."""

    __tablename__ = "finance_accounts"
    __table_args__ = (UniqueConstraint("user_id", "plaid_account_id", name="uq_fin_account"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    plaid_account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    item_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), default="")
    type: Mapped[str] = mapped_column(String(32), default="")  # depository | credit | investment ...
    mask: Mapped[str | None] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FinanceTransaction(Base):
    """Domain twin: transactions. Plaid convention: positive amount = money out."""

    __tablename__ = "finance_transactions"
    __table_args__ = (
        UniqueConstraint("user_id", "plaid_txn_id", name="uq_fin_txn"),
        Index("ix_txns_user_date", "user_id", "date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    plaid_txn_id: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False)  # plaid account id
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    name: Mapped[str] = mapped_column(String(256), default="")
    merchant: Mapped[str | None] = mapped_column(String(128))
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    category: Mapped[str] = mapped_column(String(64), default="OTHER")  # Plaid PFC primary
    pending: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WardrobeGarment(Base):
    """Domain twin: the closet. Attributes extracted by the stylist agent's
    vision pass (schema ported from styleagent's garment model)."""

    __tablename__ = "wardrobe_garments"
    __table_args__ = (Index("ix_garments_user", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="photo_upload")  # photo_upload | email_import
    photo_id: Mapped[str | None] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(256), default="")
    brand: Mapped[str | None] = mapped_column(String(128))
    type: Mapped[str] = mapped_column(String(32), default="unknown")  # top | bottom | dress | outerwear | shoes | accessory
    primary_color: Mapped[str] = mapped_column(String(32), default="")
    secondary_color: Mapped[str | None] = mapped_column(String(32))
    pattern: Mapped[str] = mapped_column(String(32), default="solid")
    material: Mapped[str | None] = mapped_column(String(48))
    formality: Mapped[str] = mapped_column(String(24), default="casual")  # casual | smart_casual | business | formal
    seasons: Mapped[dict] = mapped_column(JSON, default=dict)  # {"seasons": ["summer", ...]}
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OutfitSuggestion(Base):
    """Domain twin: generated outfits. Feedback lands in events
    (outfit_liked / outfit_rejected with target_id = this id)."""

    __tablename__ = "outfit_suggestions"
    __table_args__ = (Index("ix_outfits_user_day", "user_id", "day"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    day: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    title: Mapped[str] = mapped_column(String(128), default="")
    occasion: Mapped[str] = mapped_column(String(64), default="")
    rationale: Mapped[str] = mapped_column(Text, default="")
    items: Mapped[dict] = mapped_column(JSON, default=dict)  # {"garment_ids": [...]}
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GmailAccount(Base):
    """One connected mailbox. OAuth tokens live encrypted in token_vault
    (provider = "gmail:{email}"); this row holds sync state."""

    __tablename__ = "gmail_accounts"
    __table_args__ = (UniqueConstraint("user_id", "email", name="uq_gmail_account"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    email: Mapped[str] = mapped_column(String(128), nullable=False)
    history_id: Mapped[str] = mapped_column(String(32), default="")  # incremental sync cursor
    watch_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InboxMessage(Base):
    """Domain twin: triaged mail. Tier is the product:
    needs_reply | worth_knowing | cleared | receipt."""

    __tablename__ = "inbox_messages"
    __table_args__ = (
        UniqueConstraint("user_id", "gmail_msg_id", name="uq_inbox_msg"),
        Index("ix_inbox_user_time", "user_id", "received_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    account_email: Mapped[str] = mapped_column(String(128), nullable=False)
    gmail_msg_id: Mapped[str] = mapped_column(String(32), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(32), default="")
    from_name: Mapped[str] = mapped_column(String(128), default="")
    from_addr: Mapped[str] = mapped_column(String(128), default="")
    subject: Mapped[str] = mapped_column(String(256), default="")
    body_text: Mapped[str] = mapped_column(Text, default="")  # plain text, truncated
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    tier: Mapped[str] = mapped_column(String(16), default="pending")
    gist: Mapped[str] = mapped_column(String(256), default="")  # one-line summary
    why_now: Mapped[str] = mapped_column(String(128), default="")  # urgency chip
    clear_reason: Mapped[str] = mapped_column(String(128), default="")
    verified_clear: Mapped[bool] = mapped_column(default=False)  # adversarial pass agreed
    archived: Mapped[bool] = mapped_column(default=False)  # actually archived in Gmail
    settled: Mapped[bool] = mapped_column(default=False)  # user resolved it (sent/dismissed)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InboxDraft(Base):
    """A reply written and waiting. Nothing sends without a user tap."""

    __tablename__ = "inbox_drafts"
    __table_args__ = (Index("ix_drafts_user_status", "user_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    message_id: Mapped[str] = mapped_column(String(36), nullable=False)  # inbox_messages.id
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="waiting")  # waiting | edited | sent | dismissed
    defer_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class User(Base):
    """Sign-in identities. user_id is the short stable id every other table keys
    on; Google's sub is the external anchor. Pre-linking an email to an existing
    user_id (config: user_email_links) binds historical data to a sign-in."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("google_sub", name="uq_user_google_sub"),
        UniqueConstraint("email", name="uq_user_email"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # the user_id
    google_sub: Mapped[str | None] = mapped_column(String(64))
    email: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuthSession(Base):
    """Bearer sessions issued after Google sign-in. Only the SHA-256 of the
    token is stored; revocation = deleting the row."""

    __tablename__ = "auth_sessions"
    __table_args__ = (Index("ix_sessions_user", "user_id"),)

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InterviewSession(Base):
    """The identity-seed interview (north star §2). The transcript is a
    first-class asset, stored verbatim forever — never summarized away."""

    __tablename__ = "interview_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | completed
    section: Mapped[str] = mapped_column(String(32), default="opening")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InterviewTurn(Base):
    __tablename__ = "interview_turns"
    __table_args__ = (Index("ix_iturns_session", "session_id", "idx"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    idx: Mapped[int] = mapped_column(nullable=False)
    role: Mapped[str] = mapped_column(String(8), nullable=False)  # nano | user
    text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TokenVaultEntry(Base):
    """Encrypted-at-rest OAuth credentials (Plaid, Gmail). Most sensitive table in the app.

    Phase 0 defines the shape; encryption + first real tokens land with Phase 2 (Plaid).
    """

    __tablename__ = "token_vault"
    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_vault_identity"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)  # plaid:{item} | gmail:{email}
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
