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


class SavedContext(Base):
    """The user's own words, retained even while the search index is unavailable."""
    __tablename__ = "saved_context"
    __table_args__ = (Index("ix_saved_context_user", "user_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    indexed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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


class Person(Base):
    """The people graph: one living profile per human correspondent.
    Updated incrementally on every email in or out (extract -> update),
    injected into any composer writing to them. Records, not beliefs —
    the belief-shaped distillations stay in user_facts."""

    __tablename__ = "people"
    __table_args__ = (UniqueConstraint("user_id", "email", name="uq_people_user_email"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    name: Mapped[str] = mapped_column(String(200), default="")
    relationship: Mapped[str] = mapped_column(String(120), default="")
    tone: Mapped[str] = mapped_column(String(250), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    facts: Mapped[list | None] = mapped_column(JSON, nullable=True)
    email_count: Mapped[int] = mapped_column(Integer, default=0)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
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
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    steps: Mapped[list | None] = mapped_column(JSON, nullable=True)
    campaign_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Campaign(Base):
    """A standing scout goal: the Flycatcher pattern for anything. The
    dispatcher re-runs the errand on cadence and the person hears about it
    only when the top finding actually changes."""

    __tablename__ = "campaigns"
    __table_args__ = (Index("ix_campaigns_user", "user_id", "active"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), default="research")
    cadence_hours: Mapped[int] = mapped_column(Integer, default=24)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
    """One connected mailbox, of any provider. OAuth tokens live encrypted in
    token_vault under "{provider}:{email}"; this row holds sync state.

    The table keeps its original name so no historic event payload lies.
    """

    __tablename__ = "gmail_accounts"
    __table_args__ = (UniqueConstraint("user_id", "provider", "email",
                                       name="uq_mail_account"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # "gmail" | "outlook" | "stub". Also the vault namespace for this mailbox.
    provider: Mapped[str] = mapped_column(String(16), default="gmail", nullable=False)
    email: Mapped[str] = mapped_column(String(128), nullable=False)
    # Opaque incremental-sync cursor: a Gmail history id, or a Graph delta
    # link, which is a full URL — hence Text rather than a short string.
    history_id: Mapped[str] = mapped_column(Text, default="")
    # Committed with the recovered page. NULL means no recovery is in progress.
    recovery_state: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    history_import_state: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    sync_error: Mapped[str] = mapped_column(String(200), default="")
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    watch_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Push-notification registration id, for providers that name one.
    subscription_id: Mapped[str] = mapped_column(String(128), default="")
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
    gmail_msg_id: Mapped[str] = mapped_column(String(512), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(256), default="")
    from_name: Mapped[str] = mapped_column(String(128), default="")
    from_addr: Mapped[str] = mapped_column(String(128), default="")
    subject: Mapped[str] = mapped_column(String(256), default="")
    body_text: Mapped[str] = mapped_column(Text, default="")  # plain text, truncated
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Envelope kept at ingest. Without these, "addressed to me or to four
    # hundred people" and "is this a reply in my own thread" are unanswerable —
    # and they are the strongest signals of what matters to THIS person.
    to_addrs: Mapped[str] = mapped_column(Text, default="")        # comma-joined
    cc_addrs: Mapped[str] = mapped_column(Text, default="")
    reply_to: Mapped[str] = mapped_column(String(320), default="")
    message_id_hdr: Mapped[str] = mapped_column(String(320), default="")
    in_reply_to: Mapped[str] = mapped_column(String(320), default="")
    list_id: Mapped[str] = mapped_column(String(320), default="")
    precedence: Mapped[str] = mapped_column(String(32), default="")
    has_list_unsubscribe: Mapped[bool] = mapped_column(Boolean, default=False)
    # Deterministic features computed in code from the envelope and our own
    # history. Evidence for the model and the scoring key for evals; never
    # invented by a model, so an email cannot fake them.
    signals: Mapped[dict | None] = mapped_column(JSON, default=None)
    tier: Mapped[str] = mapped_column(String(16), default="pending")
    # The tier answers one question; these answer the two that actually differ.
    # A recall notice is important and needs no reply; a scheduling ping needs a
    # reply and is not important. Recorded now so a labelled corpus can be
    # written against them; `tier` stays authoritative for display and behaviour
    # until a golden set can prove a change of that safe.
    importance: Mapped[str] = mapped_column(String(8), default="normal")  # low | normal | high
    requires_reply: Mapped[bool] = mapped_column(Boolean, default=False)
    # Extracted from email text, so attacker-influenced: order the list by it,
    # never let it drive an action or arm a timer.
    attention_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    gist: Mapped[str] = mapped_column(String(256), default="")  # one-line summary
    why_now: Mapped[str] = mapped_column(String(128), default="")  # urgency chip
    clear_reason: Mapped[str] = mapped_column(String(128), default="")
    note_kind: Mapped[str] = mapped_column(String(120), default="")
    suspicious: Mapped[bool] = mapped_column(Boolean, default=False)
    # A "never miss" rule put this in needs_reply; the model saw no ask, so
    # no auto-reply path may answer it on its own.
    rule_promoted: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_clear: Mapped[bool] = mapped_column(default=False)  # adversarial pass agreed
    archived: Mapped[bool] = mapped_column(default=False)  # actually archived in Gmail
    settled: Mapped[bool] = mapped_column(default=False)  # user resolved it (sent/dismissed)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MailHistory(Base):
    """Conversation the twin deliberately does not hold: sent mail, archived
    mail, anything older than the working set.

    InboxMessage is a QUEUE — rows in it are triaged, drafted for, archived and
    replied to. This table is a RECORD. Nothing here is ever triaged or acted
    on; it exists so that "have I answered this person before", "what did we
    agree in March" and "who actually matters to me" have data to read. Keeping
    the two apart is what lets a deep import be safe: importing ten years of
    mail cannot produce ten years of replies, because nothing downstream of the
    queue ever looks here for work.
    """

    __tablename__ = "mail_history"
    __table_args__ = (
        UniqueConstraint("user_id", "gmail_msg_id", name="uq_mail_history_msg"),
        Index("ix_mail_history_sender", "user_id", "from_addr"),
        Index("ix_mail_history_thread", "user_id", "thread_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    account_email: Mapped[str] = mapped_column(String(128), default="")
    gmail_msg_id: Mapped[str] = mapped_column(String(512), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(256), default="")
    # 'outbound' is the half the product never had. Without the user's own
    # replies, "unanswered" is a guess and "I always reply to Priya" is unknowable.
    direction: Mapped[str] = mapped_column(String(8), default="inbound")
    from_addr: Mapped[str] = mapped_column(String(320), default="")
    to_addrs: Mapped[str] = mapped_column(Text, default="")
    subject: Mapped[str] = mapped_column(String(256), default="")
    body_text: Mapped[str] = mapped_column(Text, default="")
    # When it was SENT, not when it was imported. Everything dated by import
    # time makes a decade of history look like one very busy afternoon.
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InboxDraft(Base):
    """A reply written and waiting. Nothing sends without a user tap."""

    __tablename__ = "inbox_drafts"
    __table_args__ = (Index("ix_drafts_user_status", "user_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    message_id: Mapped[str] = mapped_column(String(36), nullable=False)  # inbox_messages.id
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="waiting")  # waiting | edited | auto_pending | sent | dismissed
    defer_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # auto_pending only: when the grace window closes and it sends itself.
    auto_send_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set whenever the person rewrote the words (also inside a window, where
    # status stays auto_pending), so "sent as written" verdicts stay honest.
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # auto_sending only: when a sender claimed the row. Staleness (a crash
    # mid-send) is measured from here, never from the deadline.
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # How the body came to be — separate from `status`, which is about delivery.
    # Only a `ready` draft may ever send itself. A refusal, a failed call, or a
    # draft still missing information sits in the inbox for the person to
    # finish; it is never auto-scheduled and send_due holds it at the deadline.
    generation_status: Mapped[str] = mapped_column(String(16), default="ready")  # ready | needs_input | failed | refused
    generation_reason: Mapped[str] = mapped_column(String(200), default="")
    # Imported private material (notes, transcripts, documents) informed these
    # words. The sender's own text chose what was recalled, so a person reads
    # this one before it goes out, however it was delegated.
    used_imported_context: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class GroceryItem(Base):
    """One thing on the shelf. The unit the whole vertical is about.

    `declared_out_at` is the person saying "I'm out" with their own eyes on
    their own cupboard. It outranks every prediction, permanently, until the
    next purchase — an assistant that argues with someone about whether they
    have milk is worse than one that never guessed.
    """

    __tablename__ = "grocery_items"
    __table_args__ = (
        UniqueConstraint("user_id", "slug", name="uq_grocery_item"),
        Index("ix_grocery_user", "user_id", "category"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False)   # normalised name
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(40), default="")
    brand: Mapped[str] = mapped_column(String(80), default="")
    size: Mapped[str] = mapped_column(String(40), default="")        # "1 gal", "500g"
    unit: Mapped[str] = mapped_column(String(24), default="")
    image_ref: Mapped[str] = mapped_column(String(200), default="")
    # Resolved once against a store's catalogue so a handoff basket contains the
    # exact product this household buys, not a search engine's guess at the name.
    product_ref: Mapped[str] = mapped_column(String(64), default="")   # UPC or product id
    product_ref_kind: Mapped[str] = mapped_column(String(12), default="")  # upc | id
    on_list: Mapped[bool] = mapped_column(Boolean, default=False)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)     # always keep stocked
    declared_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_purchased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GroceryReceipt(Base):
    """Durable extraction ledger shared by live and historical copies of mail."""
    __tablename__ = "grocery_receipts"
    __table_args__ = (UniqueConstraint("user_id", "source_ref", name="uq_grocery_receipt"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GroceryPurchase(Base):
    """A thing actually bought, once. The only input the forecast trusts.

    `source` says where the knowledge came from — a parsed receipt, a linked
    platform, or the person typing it — because a shelf built from guesses and
    a shelf built from receipts deserve different confidence.
    """

    __tablename__ = "grocery_purchases"
    __table_args__ = (
        UniqueConstraint("user_id", "source", "source_ref", "item_id",
                         name="uq_grocery_purchase"),
        Index("ix_grocery_purchase_item", "user_id", "item_id", "purchased_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    item_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="manual")  # email|platform|manual
    source_ref: Mapped[str] = mapped_column(String(512), default="")   # receipt/order id
    merchant: Mapped[str] = mapped_column(String(80), default="")
    quantity: Mapped[float] = mapped_column(Float, default=1.0)     # packages bought
    # The size of ONE package, normalised (ml | g | ct). Without it, buying a
    # half-gallon instead of a gallon looks like the same purchase and the
    # household appears to slow down.
    pack_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    pack_unit: Mapped[str] = mapped_column(String(8), default="")
    unit_price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    purchased_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GroceryOrder(Base):
    """A basket, and how far along it is.

    It exists as a row before anything is bought, for the same reason a reply
    is a draft before it is sent: spending money is tier 3, so Nano may
    assemble the basket and may never place it. `confirmed_by` records the
    person's own yes, and `place_order` refuses without it.
    """

    __tablename__ = "grocery_orders"
    __table_args__ = (Index("ix_grocery_order_user", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    platform: Mapped[str] = mapped_column(String(24), default="list")
    # draft (Nano built it) | confirmed (the person said yes) | placed | failed | cancelled
    status: Mapped[str] = mapped_column(String(16), default="draft")
    lines: Mapped[list | None] = mapped_column(JSON, default=None)
    subtotal_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(String(300), default="")   # why Nano proposed it
    # A placed order's platform id, or a handed-off basket's URL. Wide enough
    # to be a URL: truncating one produces a link that loads nothing.
    external_id: Mapped[str] = mapped_column(String(1024), default="")
    error: Mapped[str] = mapped_column(String(300), default="")
    confirmed_by: Mapped[str] = mapped_column(String(8), default="")   # "" | user
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    placed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GroceryLink(Base):
    """A shopping platform the person connected."""

    __tablename__ = "grocery_links"
    __table_args__ = (UniqueConstraint("user_id", "platform", name="uq_grocery_link"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    platform: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="linked")  # linked | revoked
    account_label: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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
    # plaid:{item} | gmail:{email} | outlook:{email} — long enough for a
    # provider prefix over a full-length address.
    provider: Mapped[str] = mapped_column(String(192), nullable=False)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
