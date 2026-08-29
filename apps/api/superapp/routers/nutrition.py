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


class MealFix(BaseModel):
    note: str = Field(min_length=3, max_length=500)


@router.post("/nutrition/meals/{meal_id}/fix")
def fix_meal_route(meal_id: str, body: MealFix, user_id: str = Depends(current_user_id),
                   db: Session = Depends(get_db)):
    """Cal Neo's ✦ Fix: 'the biryani was mutton, not veg' -> re-estimate."""
    from sqlalchemy import select

    from ..agents.nutrition import fix_meal
    from ..models import NutritionMeal

    meal = db.scalar(select(NutritionMeal).where(
        NutritionMeal.id == meal_id, NutritionMeal.user_id == user_id))
    if meal is None:
        raise HTTPException(status_code=404, detail="No such meal")
    original = {"description": meal.description, "kcal": meal.kcal or 0,
                "protein_g": meal.protein_g or 0, "carbs_g": meal.carbs_g or 0,
                "fat_g": meal.fat_g or 0, "confidence": meal.confidence}
    fix_meal(db, user_id=user_id, meal_id=meal_id, note=body.note, original=original)
    append_event(db, user_id=user_id, type="meal_fixed", agent="nutrition",
                 domain="nutrition", payload={"meal_id": meal_id, "note": body.note[:200]})
    db.commit()
    return render_screen(db, agent="nutrition", user_id=user_id).model_dump()


class OnboardBody(BaseModel):
    born_year: int | None = None
    height_cm: float | None = None
    weight_kg: float | None = None
    steps_target: int | None = None
    kcal_override: int | None = Field(default=None, ge=800, le=6000)
    water_override: int | None = Field(default=None, ge=1000, le=6000)


class SuggestBody(BaseModel):
    born_year: int
    height_cm: float
    weight_kg: float
    steps_target: int = 10000


def _nutrition_state(db: Session, user_id: str) -> dict:
    from datetime import datetime, timezone

    from ..nutrition_plan import health_score
    from ..substrate import read_facts
    from ..substrate.nutrition import meals_context

    data = meals_context(db, user_id)
    facts = {f.key: f.value for f in read_facts(db, user_id=user_id,
                                                domains=["nutrition"], limit=30)}
    plan = facts.get("plan")
    today = data["today"]
    day_n = 0
    if plan and plan.get("started"):
        try:
            started = datetime.fromisoformat(plan["started"]).date()
            day_n = (datetime.now(timezone.utc).date() - started).days + 1
        except ValueError:
            day_n = 1
    score, note = health_score(today, plan or {})
    return {
        "onboarded": plan is not None,
        "plan": plan,
        "profile": facts.get("profile"),
        "today": today,
        "week": data["week"],
        "activity": data["activity"],
        "day_n": day_n,
        "health": {"score": score, "note": note},
        "summary": (facts.get("last_summary") or {}).get("text", ""),
    }


@router.get("/nutrition/state")
def nutrition_state(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    """Everything the Cal screen renders, in one shot."""
    return _nutrition_state(db, user_id)


@router.post("/nutrition/onboard")
def onboard(body: OnboardBody, user_id: str = Depends(current_user_id),
            db: Session = Depends(get_db)):
    """Cal Neo onboarding & settings: partial fields merge; overrides respected."""
    from ..nutrition_plan import save_profile_and_plan

    incoming = {k: v for k, v in body.model_dump().items()
                if v is not None and k not in ("kcal_override", "water_override")}
    plan = save_profile_and_plan(db, user_id, incoming or {"noop": True},
                                 kcal_override=body.kcal_override,
                                 water_override=body.water_override)
    if plan is None:
        raise HTTPException(status_code=422, detail="Need at least birth year, height, and weight")
    append_event(db, user_id=user_id, type="nutrition_plan_set", agent="nutrition",
                 domain="nutrition", payload={"kcal": plan["kcal"], "via": "onboarding"})
    db.commit()
    return _nutrition_state(db, user_id)


@router.post("/nutrition/suggest")
def suggest(body: SuggestBody, user_id: str = Depends(current_user_id)):
    """Live suggestions for the targets step — computed, not stored."""
    from ..nutrition_plan import compute_plan

    plan = compute_plan({"born_year": body.born_year, "height_cm": body.height_cm,
                         "weight_kg": body.weight_kg, "steps_target": body.steps_target})
    if plan is None:
        raise HTTPException(status_code=422, detail="Out-of-range numbers")
    bmi = round(body.weight_kg / (body.height_cm / 100) ** 2, 1)
    band = ("Underweight" if bmi < 18.5 else "Healthy" if bmi < 25
            else "Overweight" if bmi < 30 else "Obese")
    return {"kcal": plan["kcal"], "water_ml": plan["water_ml"], "bmi": bmi, "bmi_band": band}


@router.post("/nutrition/reset")
def reset_nutrition(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    """Cal Neo settings: Restart onboarding — the plan and profile go, meals stay."""
    from sqlalchemy import delete

    from ..models import UserFact

    db.execute(delete(UserFact).where(UserFact.user_id == user_id,
                                      UserFact.domain == "nutrition",
                                      UserFact.key.in_(("profile", "plan"))))
    append_event(db, user_id=user_id, type="nutrition_reset", agent="nutrition",
                 domain="nutrition", payload={})
    db.commit()
    return {"ok": True}


@router.get("/media/{photo_id}")
def get_media(photo_id: str, user_id: str = Depends(current_user_id)):
    try:
        data, media_type = storage.read_photo(photo_id)
    except (ValueError, FileNotFoundError):
        raise HTTPException(status_code=404, detail="No such photo")
    return Response(content=data, media_type=media_type)
