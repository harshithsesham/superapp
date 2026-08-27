"""Permission-kernel endpoints (north star step 3).

Promotion is the only write here, and it only exists behind an explicit user
tap — the kernel itself never calls it. Demotion needs no endpoint: it happens
inside record_decision the moment a verdict is an undo.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import current_user_id
from ..db import get_db
from ..kernel import autonomy_context, evidence, promote
from ..substrate.events import append_event

router = APIRouter(prefix="/v1/kernel", tags=["kernel"])


@router.get("/autonomy")
def get_autonomy(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    return autonomy_context(db, user_id)


class PromoteBody(BaseModel):
    action_key: str


@router.post("/promote")
def promote_action(body: PromoteBody, user_id: str = Depends(current_user_id),
                   db: Session = Depends(get_db)):
    """The user's yes to 'want me to do this on my own now?'"""
    try:
        grant = promote(db, user_id=user_id, action_key=body.action_key)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    append_event(db, user_id=user_id, type="autonomy_promoted", agent="hub",
                 payload={"action_key": body.action_key, "level": grant.level,
                          "evidence": grant.evidence})
    db.commit()
    ev = evidence(db, user_id, body.action_key)
    return {"action_key": body.action_key, "level": ev.level}
