"""Nutrition vertical endpoints (Phase 1).

Ingest is synchronous by design: the upload request runs the agent's think step
and returns the fresh screen, keeping the "meal card in under ~15s" criterion
without a job queue. The daily-summary cron instead hits
POST /v1/agents/nutrition/think (see routers/screen.py).
"""
from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import storage
from ..agents.base import render_screen, run_think
from ..auth import current_user_id
from ..db import get_db
from ..substrate import append_event, create_meal, write_fact

router = APIRouter(prefix="/v1", tags=["nutrition"])


def _ingest_meal(db: Session, *, user_id: str, source: str, description: str = "",
                 photo_id: str | None = None) -> dict:
    meal = create_meal(db, user_id=user_id, source=source, description=description, photo_id=photo_id)
    append_event(
        db, user_id=user_id, type="meal_logged", agent="nutrition", domain="nutrition",
        payload={"meal_id": meal.id, "source": source},
    )
    run_think(
        db, agent="nutrition", user_id=user_id,
        trigger={"kind": f"meal_{source}", "meal_id": meal.id,
                 "description": description, "photo_id": photo_id},
    )
    return render_screen(db, agent="nutrition", user_id=user_id).model_dump()


@router.post("/nutrition/photo")
async def log_meal_photo(
    photo: UploadFile, user_id: str = Depends(current_user_id), db: Session = Depends(get_db)
):
    data = await photo.read()
    try:
        photo_id = storage.save_photo(data, photo.content_type or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _ingest_meal(db, user_id=user_id, source="photo", photo_id=photo_id)


class MealLog(BaseModel):
    description: str = Field(min_length=1, max_length=500)


@router.post("/nutrition/log")
def log_meal_text(
    body: MealLog, user_id: str = Depends(current_user_id), db: Session = Depends(get_db)
):
    return _ingest_meal(db, user_id=user_id, source="text", description=body.description)


class TargetUpdate(BaseModel):
    kcal: int = Field(ge=800, le=6000)


@router.post("/nutrition/target")
def set_target(
    body: TargetUpdate, user_id: str = Depends(current_user_id), db: Session = Depends(get_db)
):
    """User-stated target — a belief, so it lives in user_facts (source: the user,
    confidence 1.0), not in a twin."""
    write_fact(
        db, user_id=user_id, domain="nutrition", key="daily_target",
        value={"kcal": body.kcal}, confidence=1.0, source_agent="user",
    )
    db.commit()
    return {"ok": True, "kcal": body.kcal}


class WaterLog(BaseModel):
    ml: int = Field(default=250, ge=50, le=2000)


@router.post("/nutrition/water")
def log_water(body: WaterLog, user_id: str = Depends(current_user_id),
              db: Session = Depends(get_db)):
    append_event(db, user_id=user_id, type="water_logged", agent="nutrition",
                 domain="nutrition", payload={"ml": body.ml})
    db.commit()
    return render_screen(db, agent="nutrition", user_id=user_id).model_dump()


class ActivitySync(BaseModel):
    steps: int = Field(ge=0, le=200_000)
    active_kcal: int = Field(default=0, ge=0, le=20_000)


@router.post("/nutrition/activity")
def sync_activity(body: ActivitySync, user_id: str = Depends(current_user_id),
                  db: Session = Depends(get_db)):
    """The phone reports HealthKit's day so far. Latest report wins."""
    append_event(db, user_id=user_id, type="activity_synced", agent="nutrition",
                 domain="nutrition", payload={"steps": body.steps,
                                              "active_kcal": body.active_kcal})
    db.commit()
    return {"ok": True}


@router.get("/media/{photo_id}")
def get_media(photo_id: str, user_id: str = Depends(current_user_id)):
    try:
        data, media_type = storage.read_photo(photo_id)
    except (ValueError, FileNotFoundError):
        raise HTTPException(status_code=404, detail="No such photo")
    return Response(content=data, media_type=media_type)
