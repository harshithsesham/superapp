"""Nutrition domain twin operations. The only module that touches nutrition_meals —
agents go through these helpers (or read the slice), never the table.
"""
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import NutritionMeal


def create_meal(db: Session, *, user_id: str, source: str, description: str = "",
                photo_id: str | None = None) -> NutritionMeal:
    meal = NutritionMeal(user_id=user_id, source=source, description=description, photo_id=photo_id)
    db.add(meal)
    db.flush()
    return meal


def update_meal_estimate(db: Session, *, user_id: str, meal_id: str, description: str,
                         kcal: int, protein_g: float, carbs_g: float, fat_g: float,
                         confidence: float) -> NutritionMeal:
    meal = db.get(NutritionMeal, meal_id)
    if meal is None or meal.user_id != user_id:
        raise ValueError(f"No meal {meal_id!r} for user")
    meal.description = description or meal.description
    meal.kcal = kcal
    meal.protein_g = protein_g
    meal.carbs_g = carbs_g
    meal.fat_g = fat_g
    meal.confidence = confidence
    db.flush()
    return meal


def _day_start(now: datetime) -> datetime:
    # UTC day boundaries for now; per-user timezone is a later refinement.
    return datetime.combine(now.date(), time.min, tzinfo=timezone.utc)


def meals_context(db: Session, user_id: str) -> dict:
    """The nutrition slice of ContextSlice.domain_data: today's meals + totals,
    plus a week of history for pattern-spotting."""
    now = datetime.now(timezone.utc)
    today_start = _day_start(now)
    week_ago = today_start - timedelta(days=7)

    meals = list(db.scalars(
        select(NutritionMeal)
        .where(NutritionMeal.user_id == user_id, NutritionMeal.logged_at >= week_ago)
        .order_by(NutritionMeal.logged_at.desc())
        .limit(60)
    ))

    def row(m: NutritionMeal) -> dict:
        return {
            "id": m.id,
            "logged_at": m.logged_at.isoformat(),
            "source": m.source,
            "photo_id": m.photo_id,
            "description": m.description,
            "kcal": m.kcal,
            "protein_g": m.protein_g,
            "carbs_g": m.carbs_g,
            "fat_g": m.fat_g,
            "confidence": m.confidence,
        }

    def aware(dt: datetime) -> datetime:
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    today = [m for m in meals if aware(m.logged_at) >= today_start]
    return {
        "today": {
            "date": now.date().isoformat(),
            "kcal": sum(m.kcal or 0 for m in today),
            "protein_g": round(sum(m.protein_g or 0 for m in today), 1),
            "carbs_g": round(sum(m.carbs_g or 0 for m in today), 1),
            "fat_g": round(sum(m.fat_g or 0 for m in today), 1),
            "meals": [row(m) for m in today],
        },
        "recent_meals": [row(m) for m in meals],
    }
