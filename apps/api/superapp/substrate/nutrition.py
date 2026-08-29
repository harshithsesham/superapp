"""Nutrition domain twin operations. The only module that touches nutrition_meals —
agents go through these helpers (or read the slice), never the table.
"""
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Event, NutritionMeal


def create_meal(db: Session, *, user_id: str, source: str, description: str = "",
                photo_id: str | None = None) -> NutritionMeal:
    meal = NutritionMeal(user_id=user_id, source=source, description=description, photo_id=photo_id)
    db.add(meal)
    db.flush()
    return meal


def update_meal_estimate(db: Session, *, user_id: str, meal_id: str, description: str,
                         kcal: int, protein_g: float, carbs_g: float, fat_g: float,
                         confidence: float, fiber_g: float | None = None,
                         sugar_g: float | None = None,
                         sodium_mg: float | None = None) -> NutritionMeal:
    meal = db.get(NutritionMeal, meal_id)
    if meal is None or meal.user_id != user_id:
        raise ValueError(f"No meal {meal_id!r} for user")
    meal.description = description or meal.description
    meal.kcal = kcal
    meal.protein_g = protein_g
    meal.carbs_g = carbs_g
    meal.fat_g = fat_g
    meal.confidence = confidence
    if fiber_g is not None:
        meal.fiber_g = fiber_g
    if sugar_g is not None:
        meal.sugar_g = sugar_g
    if sodium_mg is not None:
        meal.sodium_mg = sodium_mg
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
    week_ago = today_start - timedelta(days=14)

    meals = list(db.scalars(
        select(NutritionMeal)
        .where(NutritionMeal.user_id == user_id, NutritionMeal.logged_at >= week_ago)
        .order_by(NutritionMeal.logged_at.desc())
        .limit(120)
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
            "fiber_g": m.fiber_g,
            "sugar_g": m.sugar_g,
            "sodium_mg": m.sodium_mg,
            "confidence": m.confidence,
        }

    def aware(dt: datetime) -> datetime:
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    today = [m for m in meals if aware(m.logged_at) >= today_start]

    # Water + device activity live in events (small daily records, not beliefs).
    water_ml = sum(
        e.payload.get("ml", 0) for e in db.scalars(
            select(Event).where(Event.user_id == user_id, Event.type == "water_logged",
                                Event.created_at >= today_start))
    )
    activity_event = db.scalar(
        select(Event).where(Event.user_id == user_id, Event.type == "activity_synced",
                            Event.created_at >= today_start)
        .order_by(Event.created_at.desc()))
    activity = ({"steps": activity_event.payload.get("steps", 0),
                 "active_kcal": activity_event.payload.get("active_kcal", 0)}
                if activity_event else None)

    # Two weeks, day by day (today last) — the strip scrolls, the chart
    # takes the trailing seven.
    week = []
    for d in range(13, -1, -1):
        day0 = today_start - timedelta(days=d)
        day1 = day0 + timedelta(days=1)
        day_meals = [m for m in meals if day0 <= aware(m.logged_at) < day1]
        week.append({"date": day0.date().isoformat(),
                     "day": day0.strftime("%a"),
                     "kcal": sum(m.kcal or 0 for m in day_meals),
                     "meals": len(day_meals)})

    # Streak: consecutive days with at least one meal, counting back from
    # today (or yesterday, so a fresh morning doesn't read as a broken run).
    logged_days = {aware(dt).date() for dt in db.scalars(
        select(NutritionMeal.logged_at).where(NutritionMeal.user_id == user_id)
        .order_by(NutritionMeal.logged_at.desc()).limit(400))}
    streak = 0
    cursor = now.date()
    if cursor not in logged_days:
        cursor = cursor - timedelta(days=1)
    while cursor in logged_days:
        streak += 1
        cursor = cursor - timedelta(days=1)

    return {
        "today": {
            "date": now.date().isoformat(),
            "kcal": sum(m.kcal or 0 for m in today),
            "protein_g": round(sum(m.protein_g or 0 for m in today), 1),
            "carbs_g": round(sum(m.carbs_g or 0 for m in today), 1),
            "fat_g": round(sum(m.fat_g or 0 for m in today), 1),
            "fiber_g": round(sum(m.fiber_g or 0 for m in today), 1),
            "sugar_g": round(sum(m.sugar_g or 0 for m in today), 1),
            "sodium_mg": round(sum(m.sodium_mg or 0 for m in today)),
            "water_ml": int(water_ml),
            "meals": [row(m) for m in today],
        },
        "activity": activity,
        "week": week,
        "streak_days": streak,
        "recent_meals": [row(m) for m in meals],
    }
