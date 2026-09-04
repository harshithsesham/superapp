"""The scout's task queue (agentic phase 1a).

People (and the orb) queue research errands; the browser worker pulls them
over a worker-token API, runs the web, and reports a structured shortlist.
Completion pushes to the lock screen and lands in the event ledger, so the
Hub timeline and the orb both know what was found.
"""
import hmac
import re

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user_id
from ..config import get_settings
from ..db import get_db
from ..dispatcher import dispatch_tick, retry_or_fail, settle_campaign_check
from ..models import AgentTask, Campaign, FlightWatch, utcnow
from ..push import send_push
from ..substrate.events import append_event

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])


def _worker_auth(authorization: str = Header(default="")) -> None:
    settings = get_settings()
    presented = authorization.removeprefix("Bearer ").strip()
    if not (settings.worker_token
            and hmac.compare_digest(presented, settings.worker_token)):
        raise HTTPException(status_code=401, detail="Bad worker token")


class NewTask(BaseModel):
    instruction: str = Field(min_length=8, max_length=1000)
    kind: str = "research"


@router.post("")
def create_task(body: NewTask, user_id: str = Depends(current_user_id),
                db: Session = Depends(get_db)):
    task = AgentTask(user_id=user_id, kind=body.kind[:32],
                     instruction=body.instruction.strip())
    db.add(task)
    db.flush()
    append_event(db, user_id=user_id, type="task_queued", agent="scout",
                 payload={"task_id": task.id, "instruction": task.instruction[:200]})
    db.commit()
    return {"id": task.id, "status": task.status}


def _login_url() -> str | None:
    settings = get_settings()
    if not settings.scout_session_token:
        return None
    return f"{settings.scout_public_base}/scout/r/{settings.scout_session_token}"


@router.get("")
def list_tasks(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    rows = db.scalars(select(AgentTask).where(AgentTask.user_id == user_id)
                      .order_by(AgentTask.created_at.desc()).limit(10))
    return {"login_url": _login_url(), "tasks": [{
        "id": t.id, "kind": t.kind, "instruction": t.instruction,
        "status": t.status, "result": t.result, "error": t.error,
        "created_at": t.created_at.isoformat(),
    } for t in rows]}


# ---- flight watches (the Flycatcher) ---------------------------------------

_PRICE_NUM = re.compile(r"\$\s*([\d,]+)")


def _min_price(result: dict | None) -> int | None:
    prices = []
    for item in (result or {}).get("shortlist", []):
        m = _PRICE_NUM.search(str(item.get("price", "")))
        if m:
            prices.append(int(m.group(1).replace(",", "")))
    return min(prices) if prices else None


class NewWatch(BaseModel):
    instruction: str = Field(min_length=8, max_length=500)
    target_price: int | None = Field(default=None, ge=1, le=100000)


@router.post("/watch")
def create_watch(body: NewWatch, user_id: str = Depends(current_user_id),
                 db: Session = Depends(get_db)):
    watch = FlightWatch(user_id=user_id, instruction=body.instruction.strip(),
                        target_price=body.target_price)
    db.add(watch)
    db.flush()
    task = AgentTask(user_id=user_id, kind="flights",
                     instruction=watch.instruction, watch_id=watch.id)
    db.add(task)
    append_event(db, user_id=user_id, type="task_queued", agent="scout",
                 payload={"watch_id": watch.id, "kind": "flight_watch",
                          "instruction": watch.instruction[:200]})
    db.commit()
    return {"id": watch.id, "first_check": task.id}


@router.get("/watches")
def list_watches(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    rows = db.scalars(select(FlightWatch).where(FlightWatch.user_id == user_id,
                                                FlightWatch.active.is_(True))
                      .order_by(FlightWatch.created_at.desc()).limit(10))
    return {"watches": [{
        "id": w.id, "instruction": w.instruction, "target_price": w.target_price,
        "best_price": w.best_price, "created_at": w.created_at.isoformat(),
    } for w in rows]}


@router.delete("/watch/{watch_id}")
def stop_watch(watch_id: str, user_id: str = Depends(current_user_id),
               db: Session = Depends(get_db)):
    watch = db.get(FlightWatch, watch_id)
    if watch is None or watch.user_id != user_id:
        raise HTTPException(status_code=404, detail="No such watch")
    watch.active = False
    watch.updated_at = utcnow()
    db.commit()
    return {"ok": True}


@router.post("/flight-watch-tick", dependencies=[Depends(_worker_auth)])
def flight_watch_tick(db: Session = Depends(get_db)):
    """Cron calls this daily: queue one fresh check per active watch."""
    queued = 0
    for watch in db.scalars(select(FlightWatch).where(FlightWatch.active.is_(True))):
        pending = db.scalar(select(AgentTask).where(
            AgentTask.watch_id == watch.id,
            AgentTask.status.in_(("queued", "running"))).limit(1))
        if pending is not None:
            continue
        db.add(AgentTask(user_id=watch.user_id, kind="flights",
                         instruction=watch.instruction, watch_id=watch.id))
        queued += 1
    db.commit()
    return {"queued": queued}


def _settle_watch_check(db: Session, task: AgentTask, result: dict) -> None:
    """A watch check landed: push only on a hit target or a new low."""
    watch = db.get(FlightWatch, task.watch_id)
    if watch is None or not watch.active:
        return
    price = _min_price(result)
    watch.updated_at = utcnow()
    if price is None:
        return
    hit_target = watch.target_price is not None and price <= watch.target_price
    new_low = watch.best_price is None or price < watch.best_price
    prev_best = watch.best_price
    watch.best_price = price if new_low else watch.best_price
    if hit_target or (new_low and prev_best is not None):
        was = f" (was ${prev_best})" if prev_best is not None else ""
        send_push(db, user_id=watch.user_id, title="Nano — flight deal",
                  body=f"{watch.instruction[:80]}: now ${price}{was}. "
                       "Open the scout card to book.",
                  agent="scout")


# ---- worker side -----------------------------------------------------------

@router.get("/next", dependencies=[Depends(_worker_auth)])
def next_task(db: Session = Depends(get_db)):
    from sqlalchemy import or_
    task = db.scalar(select(AgentTask).where(
        AgentTask.status == "queued",
        or_(AgentTask.next_attempt_at.is_(None),
            AgentTask.next_attempt_at <= utcnow()))
        .order_by(AgentTask.created_at).limit(1))
    if task is None:
        return {"task": None}
    task.status = "running"
    task.updated_at = utcnow()
    db.commit()
    return {"task": {"id": task.id, "user_id": task.user_id, "kind": task.kind,
                     "instruction": task.instruction}}


class TaskResult(BaseModel):
    result: dict


class TaskError(BaseModel):
    error: str = Field(max_length=512)


@router.post("/{task_id}/complete", dependencies=[Depends(_worker_auth)])
def complete_task(task_id: str, body: TaskResult, db: Session = Depends(get_db)):
    task = db.get(AgentTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="No such task")
    # Running is the normal case; queued means the lease reclaimed it while
    # the worker was actually still finishing — accept the late result and
    # cancel the redundant retry. Done/failed are settled: ignore.
    if task.status not in ("running", "queued"):
        return {"ok": True, "ignored": task.status}
    task.status = "done"
    task.result = body.result
    task.next_attempt_at = None
    task.updated_at = utcnow()
    append_event(db, user_id=task.user_id, type="task_completed", agent="scout",
                 payload={"task_id": task.id,
                          "summary": str(body.result.get("summary", ""))[:300],
                          "found": len(body.result.get("shortlist", []))})
    if task.watch_id:
        _settle_watch_check(db, task, body.result)
        db.commit()
        return {"ok": True}
    if task.campaign_id:
        settle_campaign_check(db, task, body.result, send_push)
        db.commit()
        return {"ok": True}
    n = len(body.result.get("shortlist", []))
    summary = str(body.result.get("summary", "")).strip()
    if body.result.get("connect"):
        send_push(db, user_id=task.user_id, title="Nano — login window ready",
                  body="Open Nano and tap Log in on the scout card. "
                       "The window stays open 20 minutes.",
                  agent="scout")
    else:
        send_push(db, user_id=task.user_id, title="Nano — scouted",
                  body=(summary or f"Found {n} options for: {task.instruction[:80]}")[:170],
                  agent="scout")
    db.commit()
    return {"ok": True}


@router.post("/{task_id}/fail", dependencies=[Depends(_worker_auth)])
def fail_task(task_id: str, body: TaskError, db: Session = Depends(get_db)):
    task = db.get(AgentTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="No such task")
    status = retry_or_fail(db, task, body.error)
    db.commit()
    return {"ok": True, "status": status}


@router.post("/dispatch-tick", dependencies=[Depends(_worker_auth)])
def dispatch(db: Session = Depends(get_db)):
    """Cron calls this every ten minutes: the durable-runs spine — reclaim
    stuck tasks, retry the retryable, queue due campaign checks."""
    out = dispatch_tick(db)
    db.commit()
    return out


@router.get("/campaigns")
def list_campaigns(user_id: str = Depends(current_user_id),
                   db: Session = Depends(get_db)):
    rows = db.scalars(select(Campaign).where(
        Campaign.user_id == user_id, Campaign.active.is_(True))
        .order_by(Campaign.created_at.desc()).limit(10))
    return {"campaigns": [
        {"id": c.id, "goal": c.goal, "cadence_hours": c.cadence_hours,
         "last_top": (c.state or {}).get("last_top", ""),
         "runs": (c.state or {}).get("runs", 0),
         "created_at": c.created_at.isoformat()} for c in rows]}


@router.delete("/campaign/{campaign_id}")
def stop_campaign(campaign_id: str, user_id: str = Depends(current_user_id),
                  db: Session = Depends(get_db)):
    camp = db.get(Campaign, campaign_id)
    if camp is None or camp.user_id != user_id:
        raise HTTPException(status_code=404, detail="No such campaign")
    camp.active = False
    camp.updated_at = utcnow()
    db.commit()
    return {"ok": True}
