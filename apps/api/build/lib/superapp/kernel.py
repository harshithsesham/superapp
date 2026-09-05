"""The permission kernel (north star step 3, Nano V4 "Without asking").

One ladder for every capability in the app:

    L0 observe · L1 draft · L2 ask first · L3 act + report · L4 silent

Autonomy is EARNED, NOT CONFIGURED. There is no settings page: a capability
starts at its default level, every user verdict lands in the `decisions`
ledger, and promotion is offered only when the record is clean — then granted
only with the user's explicit yes. One undo demotes immediately, no appeal.

Hard invariants (never promotable past L2, regardless of record):
money movement, anything sent to a new recipient, deletions.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AutonomyGrant, Decision

# Capability -> default ladder level. L2 = drafts + asks (today's whole app).
DEFAULT_LEVELS = {
    "inbox.send_reply": 2,   # draft waits for your yes
    "inbox.archive_noise": 3,  # files noise, reports in the ledger
    "inbox.flag_to_read": 3,
    "nutrition.log_meal": 3,   # estimates + logs, shows its work
    "finance.categorize": 3,
    "stylist.suggest": 1,
}

# Never promotable past this cap, no matter how clean the record.
HARD_CAPS = {
    "inbox.send_reply": 4,          # can eventually earn silence...
    "inbox.send_new_recipient": 2,  # ...but never to someone new
    "finance.move_money": 0,        # money never moves itself
    "inbox.delete": 2,
}

PROMOTION_MIN_DECISIONS = 20
PROMOTION_CLEAN_RATE = 0.95  # accepted-unedited share, per the north star


@dataclass
class ActionEvidence:
    action_key: str
    level: int
    accepted: int = 0
    edited: int = 0
    rejected: int = 0
    undone: int = 0
    acted: int = 0  # nano acted autonomously (L3+) — reported, not asked
    total_user: int = 0
    clean_rate: float = 0.0
    promotable: bool = False
    last_demotion_reason: str = ""


def record_decision(db: Session, *, user_id: str, agent: str, action_key: str,
                    decided_by: str, verdict: str, payload: dict | None = None) -> Decision:
    row = Decision(user_id=user_id, agent=agent, action_key=action_key,
                   decided_by=decided_by, verdict=verdict, payload=payload or {})
    db.add(row)
    db.flush()
    if verdict == "undone":
        demote(db, user_id=user_id, action_key=action_key, reason="user undid an action")
    return row


def _active_grant(db: Session, user_id: str, action_key: str) -> AutonomyGrant | None:
    return db.scalar(
        select(AutonomyGrant)
        .where(AutonomyGrant.user_id == user_id,
               AutonomyGrant.action_key == action_key,
               AutonomyGrant.revoked_at.is_(None))
        .order_by(AutonomyGrant.created_at.desc()))


def current_level(db: Session, user_id: str, action_key: str) -> int:
    grant = _active_grant(db, user_id, action_key)
    level = grant.level if grant else DEFAULT_LEVELS.get(action_key, 2)
    return min(level, HARD_CAPS.get(action_key, 4))


def evidence(db: Session, user_id: str, action_key: str) -> ActionEvidence:
    rows = list(db.scalars(select(Decision).where(
        Decision.user_id == user_id, Decision.action_key == action_key)))
    ev = ActionEvidence(action_key=action_key,
                        level=current_level(db, user_id, action_key))
    for r in rows:
        if r.decided_by == "nano":
            ev.acted += 1
        elif r.verdict in ("accepted", "edited", "rejected", "undone"):
            setattr(ev, r.verdict, getattr(ev, r.verdict) + 1)
            ev.total_user += 1
    if ev.total_user:
        ev.clean_rate = ev.accepted / ev.total_user
    cap = HARD_CAPS.get(action_key, 4)
    ev.promotable = (ev.level < cap
                     and ev.total_user >= PROMOTION_MIN_DECISIONS
                     and ev.clean_rate >= PROMOTION_CLEAN_RATE
                     and ev.undone == 0)
    last = db.scalar(select(AutonomyGrant).where(
        AutonomyGrant.user_id == user_id, AutonomyGrant.action_key == action_key,
        AutonomyGrant.revoked_at.is_not(None))
        .order_by(AutonomyGrant.revoked_at.desc()))
    if last:
        ev.last_demotion_reason = last.revoke_reason
    return ev


def promote(db: Session, *, user_id: str, action_key: str) -> AutonomyGrant:
    """One level up — only ever called from an explicit user yes."""
    ev = evidence(db, user_id, action_key)
    if not ev.promotable:
        raise ValueError(f"{action_key} has not earned a promotion "
                         f"({ev.total_user} decisions, {ev.clean_rate:.0%} clean)")
    grant = AutonomyGrant(
        user_id=user_id, action_key=action_key, level=ev.level + 1,
        granted_by="user",
        evidence={"accepted": ev.accepted, "edited": ev.edited,
                  "rejected": ev.rejected, "clean_rate": round(ev.clean_rate, 3)})
    db.add(grant)
    db.flush()
    return grant


def demote(db: Session, *, user_id: str, action_key: str, reason: str) -> None:
    """One undo takes it back down — immediately, no appeal."""
    grant = _active_grant(db, user_id, action_key)
    if grant is None:
        return  # already at the default level; nothing granted to revoke
    grant.revoked_at = datetime.now(timezone.utc)
    grant.revoke_reason = reason[:128]


def autonomy_context(db: Session, user_id: str) -> dict:
    """The Hub's "Without asking" panel — every capability with a record."""
    keys = set(DEFAULT_LEVELS) | {
        k for (k,) in db.execute(select(Decision.action_key).where(
            Decision.user_id == user_id).distinct())}
    out = []
    for key in sorted(keys):
        ev = evidence(db, user_id, key)
        if ev.total_user or ev.acted or ev.level >= 3:
            out.append({
                "action_key": key, "level": ev.level, "acted": ev.acted,
                "accepted": ev.accepted, "edited": ev.edited,
                "rejected": ev.rejected, "undone": ev.undone,
                "total_user": ev.total_user, "clean_rate": ev.clean_rate,
                "promotable": ev.promotable,
                "last_demotion_reason": ev.last_demotion_reason,
            })
    return {"capabilities": out}
