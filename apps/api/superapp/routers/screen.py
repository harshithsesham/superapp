"""Screen + interaction endpoints: the client is a thin renderer over these."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..agents.base import run_agent
from ..auth import current_user_id
from ..db import get_db
from ..substrate import append_event

router = APIRouter(prefix="/v1", tags=["screens"])

# Which agent renders which screen. Deterministic routing — the orchestrator
# only exists for the chat surface (Phase 5), not for screens.
SCREEN_AGENTS = {"home": "demo"}


@router.get("/screen/{name}")
def get_screen(name: str, user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    agent = SCREEN_AGENTS.get(name)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"No screen named {name!r}")
    result = run_agent(db, agent=agent, user_id=user_id, trigger={"kind": "screen_view", "screen": name})
    return result.screen.model_dump() if result.screen else {"type": "screen", "title": name, "sections": []}


class UserReaction(BaseModel):
    """Dismissals, taps, edits — the best training signal we have (architecture §6.2)."""

    kind: str  # insight_dismissed | action_tapped | draft_edited | outfit_rejected ...
    target_id: str
    agent: str | None = None
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
        payload={"target_id": reaction.target_id, **reaction.payload},
    )
    db.commit()
    return {"ok": True}
