"""The dispatcher (Nano 2.0 Phase B): the durable-runs spine.

One cron tick owns everything the scout's queue used to lose: tasks stuck
in "running" after a worker crash are reclaimed, transient failures retry
with backoff instead of dying, and campaigns — standing goals — re-queue
their errand on cadence. Every state change lands in the event ledger, so
the heartbeat and the Hub timeline see only TERMINAL failures.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AgentTask, Campaign, utcnow
from .substrate.events import append_event

MAX_ATTEMPTS = 3          # first run + two retries
LEASE_MINUTES = 30        # a healthy scout errand finishes in ~2; login windows hold 20
BACKOFF_MINUTES = (5, 20)  # after 1st and 2nd failure


def _step(task: AgentTask, note: str) -> None:
    steps = list(task.steps or [])
    steps.append({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "note": note[:120]})
    task.steps = steps[-20:]


def retry_or_fail(db: Session, task: AgentTask, error: str) -> str:
    """A failure came in (worker /fail, or a reclaim): retry with backoff
    while attempts remain, else go terminal. Returns the new status.
    Cancellations are the user's word and never retried.

    Only a RUNNING task can fail. A /fail landing on a task that already
    completed (worker lost the /complete response and reported the whole
    cycle as failed) or was already reclaimed must not resurrect or
    double-count it — finished work stays finished."""
    if task.status != "running":
        return task.status
    cancelled = error == "Cancelled by you." or task.error == "Cancelled by you."
    task.attempts = (task.attempts or 0) + 1
    task.error = error[:500]
    task.updated_at = utcnow()
    if cancelled or task.attempts >= MAX_ATTEMPTS:
        task.status = "failed"
        _step(task, f"failed for good: {error[:80]}")
        append_event(db, user_id=task.user_id, type="task_failed", agent="scout",
                     payload={"task_id": task.id, "error": error[:200],
                              "attempts": task.attempts})
        return "failed"
    delay = BACKOFF_MINUTES[min(task.attempts - 1, len(BACKOFF_MINUTES) - 1)]
    task.status = "queued"
    task.next_attempt_at = utcnow() + timedelta(minutes=delay)
    _step(task, f"attempt {task.attempts} failed ({error[:60]}); retrying in {delay}m")
    append_event(db, user_id=task.user_id, type="task_retry", agent="scout",
                 payload={"task_id": task.id, "attempt": task.attempts,
                          "retry_in_minutes": delay, "error": error[:200]})
    return "queued"


def dispatch_tick(db: Session) -> dict:
    """One pass of the spine: reclaim, then schedule campaigns."""
    now = utcnow()
    reclaimed = 0

    stale_cutoff = now - timedelta(minutes=LEASE_MINUTES)
    for task in db.scalars(select(AgentTask).where(
            AgentTask.status == "running", AgentTask.updated_at < stale_cutoff)):
        retry_or_fail(db, task, "worker never reported back")
        reclaimed += 1

    campaigns_queued = 0
    for camp in db.scalars(select(Campaign).where(
            Campaign.active.is_(True),
            Campaign.next_run_at.isnot(None), Campaign.next_run_at <= now)):
        pending = db.scalar(select(AgentTask).where(
            AgentTask.campaign_id == camp.id,
            AgentTask.status.in_(("queued", "running"))).limit(1))
        if pending is not None:
            continue
        db.add(AgentTask(user_id=camp.user_id, kind=camp.kind,
                         instruction=camp.goal, campaign_id=camp.id))
        camp.last_run_at = now
        camp.next_run_at = now + timedelta(hours=camp.cadence_hours)
        camp.updated_at = now
        append_event(db, user_id=camp.user_id, type="task_queued", agent="scout",
                     payload={"campaign_id": camp.id, "kind": "campaign_check",
                              "goal": camp.goal[:200]})
        campaigns_queued += 1

    return {"reclaimed": reclaimed, "campaigns_queued": campaigns_queued}


def settle_campaign_check(db: Session, task: AgentTask, result: dict,
                          send_push) -> None:
    """A campaign check landed. The Flycatcher rule, generalized: remember
    everything, speak only when the top finding changes."""
    camp = db.get(Campaign, task.campaign_id)
    if camp is None or not camp.active:
        return
    state = dict(camp.state or {})
    shortlist = result.get("shortlist") or []
    top = shortlist[0] if shortlist else {}
    new_top = f"{top.get('title', '')} | {top.get('price', '')}".strip(" |")
    prev_top = state.get("last_top", "")
    state.update({
        "last_top": new_top or prev_top,
        "last_summary": str(result.get("summary", ""))[:300],
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "runs": int(state.get("runs", 0)) + 1,
    })
    camp.state = state
    camp.updated_at = utcnow()
    if new_top and new_top != prev_top:
        was = f" (was: {prev_top[:60]})" if prev_top else ""
        send_push(db, user_id=camp.user_id, title="Nano — still on it",
                  body=f"{camp.goal[:70]}: {new_top[:90]}{was}"[:170],
                  agent="scout")
