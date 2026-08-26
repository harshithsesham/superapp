"""Screen + interaction endpoints: the client is a thin renderer over these.

GET /screen/* is a pure read (render tier) — agents' LLM cognition only runs
through the think tier: POST /screen/*/refresh (user pull) or
POST /agents/*/think (cron/webhooks).
"""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..agents.base import get_agent, render_screen, run_think
from ..auth import current_user_id
from ..db import get_db
from ..substrate import append_event

router = APIRouter(prefix="/v1", tags=["screens"])

# Which agent renders which screen. Deterministic routing — the orchestrator
# only exists for the chat surface (Phase 5), not for screens.
SCREEN_AGENTS = {"hub": "hub", "home": "nutrition", "finance": "finance", "stylist": "stylist", "inbox": "inbox"}


def _screen_agent(name: str) -> str:
    agent = SCREEN_AGENTS.get(name)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"No screen named {name!r}")
    return agent


@router.get("/screen/{name}")
def get_screen(name: str, user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    """Pure render from the substrate — no agent cognition, no fact writes."""
    agent = _screen_agent(name)
    screen = render_screen(db, agent=agent, user_id=user_id)
    # View telemetry lives in events (excluded from context slices).
    append_event(db, user_id=user_id, type="screen_view", agent=agent, payload={"screen": name})
    db.commit()
    return screen.model_dump()


def _background_think(agent: str, user_id: str, trigger: dict) -> None:
    """Own session: runs after the response has been sent."""
    from ..db import SessionLocal

    db = SessionLocal()
    try:
        run_think(db, agent=agent, user_id=user_id, trigger=trigger)
    finally:
        db.close()


@router.post("/screen/{name}/refresh")
def refresh_screen(name: str, background: BackgroundTasks,
                   user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    """Pull-to-refresh: run the screen agent's think step, then render fresh.
    Agents with slow cognition (the inbox triaging N emails) respond immediately
    and keep thinking in the background — the client re-fetches to catch up."""
    agent = _screen_agent(name)
    spec = get_agent(agent)
    trigger = {"kind": "user_refresh", "screen": name}
    if spec.think is not None:
        if spec.slow_think:
            background.add_task(_background_think, agent, user_id, trigger)
        else:
            run_think(db, agent=agent, user_id=user_id, trigger=trigger)
    screen = render_screen(db, agent=agent, user_id=user_id)
    append_event(db, user_id=user_id, type="screen_view", agent=agent, payload={"screen": name, "refresh": True})
    db.commit()
    return screen.model_dump()


@router.post("/agents/{name}/think")
def think(name: str, user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    """Background trigger (cron/webhook): cognition + write-backs, no UI."""
    try:
        spec = get_agent(name)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"No agent named {name!r}")
    if spec.think is None:
        raise HTTPException(status_code=400, detail=f"Agent {name!r} has no think step")
    return run_think(db, agent=name, user_id=user_id, trigger={"kind": "scheduled"})


class UserReaction(BaseModel):
    """Dismissals, taps, edits — the best training signal we have (architecture §6.2)."""

    kind: str  # insight_dismissed | action_tapped | draft_edited | outfit_rejected ...
    target_id: str
    agent: str | None = None
    domain: str | None = None  # scopes the event like a fact; None = cross-domain
    payload: dict = {}


@router.post("/reactions")
def post_reaction(
    reaction: UserReaction,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    append_event(
        db,
        user_id=user_id,
        type=reaction.kind,
        agent=reaction.agent,
        domain=reaction.domain,
        payload={"target_id": reaction.target_id, **reaction.payload},
    )
    db.commit()
    return {"ok": True}
