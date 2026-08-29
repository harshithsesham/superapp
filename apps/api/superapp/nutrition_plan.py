"""The daily nutrition plan — computed, never configured (Cal AI's onboarding
outcome, earned from a one-minute conversation with the orb instead of
fifteen form screens).

Mifflin-St Jeor BMR x activity, adjusted for the goal; protein anchored to
bodyweight, fat to calories, carbs take the rest.
"""
from datetime import datetime, timezone

ACTIVITY = {"limited": 1.2, "moderate": 1.375, "athlete": 1.55}
GOAL_ADJUST = {"lose": -400, "maintain": 0, "gain": 300}

PROFILE_KEYS = {"sex", "born_year", "height_cm", "weight_kg", "target_weight_kg",
                "activity", "goal"}


def compute_plan(profile: dict) -> dict | None:
    try:
        weight = float(profile["weight_kg"])
        height = float(profile["height_cm"])
        age = max(10, datetime.now(timezone.utc).year - int(profile["born_year"]))
        sex = str(profile.get("sex", "other")).lower()
        activity = ACTIVITY.get(str(profile.get("activity", "limited")).lower(), 1.2)
        goal = str(profile.get("goal", "")).lower()
    except (KeyError, TypeError, ValueError):
        return None
    target_w = profile.get("target_weight_kg")
    if not goal:
        # Derive the goal from where they want their weight to go.
        try:
            delta = float(target_w) - weight if target_w is not None else 0.0
        except (TypeError, ValueError):
            delta = 0.0
        goal = "lose" if delta < -1.5 else "gain" if delta > 1.5 else "maintain"
    bmr = 10 * weight + 6.25 * height - 5 * age + (5 if sex == "male" else -161 if sex == "female" else -78)
    kcal = int(bmr * activity + GOAL_ADJUST.get(goal, 0))
    protein_g = round(weight * 1.7)
    fat_g = round(kcal * 0.25 / 9)
    carbs_g = round((kcal - protein_g * 4 - fat_g * 9) / 4)
    plan = {"kcal": kcal, "protein_g": protein_g, "carbs_g": max(carbs_g, 0),
            "fat_g": fat_g, "water_ml": int(weight * 35), "goal": goal}
    if target_w is not None:
        try:
            plan["target_weight_kg"] = float(target_w)
        except (TypeError, ValueError):
            pass
    return plan


def sanitize_profile(raw: dict) -> dict:
    """Whitelist + clamp what the voice model collected."""
    out: dict = {}
    for k in PROFILE_KEYS & set(raw.keys()):
        v = raw[k]
        if k in ("weight_kg", "height_cm", "target_weight_kg"):
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if not (20 <= v <= 350):
                continue
        if k == "born_year":
            try:
                v = int(v)
            except (TypeError, ValueError):
                continue
            if not (1920 <= v <= datetime.now(timezone.utc).year - 5):
                continue
        if k in ("sex", "activity", "goal"):
            v = str(v).lower()[:16]
        out[k] = v
    return out
