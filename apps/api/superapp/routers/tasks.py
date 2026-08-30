"""The scout's task queue (agentic phase 1a).

People (and the orb) queue research errands; the browser worker pulls them
over a worker-token API, runs the web, and reports a structured shortlist.
Completion pushes to the lock screen and lands in the event ledger, so the
Hub timeline and the orb both know what was found.
"""
import hmac

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user_id
from ..config import get_settings
from ..db import get_db
from ..models import AgentTask, utcnow
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


@router.get("")
def list_tasks(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    rows = db.scalars(select(AgentTask).where(AgentTask.user_id == user_id)
                      .order_by(AgentTask.created_at.desc()).limit(10))
    return {"tasks": [{
        "id": t.id, "kind": t.kind, "instruction": t.instruction,
        "status": t.status, "result": t.result, "error": t.error,
        "created_at": t.created_at.isoformat(),
    } for t in rows]}


# ---- worker side -----------------------------------------------------------

@router.get("/next", dependencies=[Depends(_worker_auth)])
def next_task(db: Session = Depends(get_db)):
    task = db.scalar(select(AgentTask).where(AgentTask.status == "queued")
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
    task.status = "done"
    task.result = body.result
    task.updated_at = utcnow()
    append_event(db, user_id=task.user_id, type="task_completed", agent="scout",
                 payload={"task_id": task.id,
                          "summary": str(body.result.get("summary", ""))[:300],
                          "found": len(body.result.get("shortlist", []))})
    n = len(body.result.get("shortlist", []))
    summary = str(body.result.get("summary", "")).strip()
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
    task.status = "failed"
    task.error = body.error
    task.updated_at = utcnow()
    append_event(db, user_id=task.user_id, type="task_failed", agent="scout",
                 payload={"task_id": task.id, "error": body.error[:200]})
    db.commit()
    return {"ok": True}
