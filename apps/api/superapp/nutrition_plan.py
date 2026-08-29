"""The daily nutrition plan — computed, never configured (Cal AI's onboarding
outcome, earned from a one-minute conversation with the orb instead of
fifteen form screens).

Mifflin-St Jeor BMR x activity, adjusted for the goal; protein anchored to
bodyweight, fat to calories, carbs take the rest.
"""
from datetime import datetime, timezone

ACTIVITY = {"limited": 1.2, "moderate": 1.375, "athlete": 1.55}
# Cal Neo asks activity as a daily-steps target; map it to a multiplier.
STEPS_ACTIVITY = {6000: 1.2, 8000: 1.375, 10000: 1.55, 12000: 1.725}
GOAL_ADJUST = {"lose": -400, "maintain": 0, "gain": 300}

PROFILE_KEYS = {"sex", "born_year", "height_cm", "weight_kg", "target_weight_kg",
                "activity", "goal", "steps_target"}


def compute_plan(profile: dict) -> dict | None:
    try:
        weight = float(profile["weight_kg"])
        height = float(profile["height_cm"])
        age = max(10, datetime.now(timezone.utc).year - int(profile["born_year"]))
        sex = str(profile.get("sex", "other")).lower()
        steps_target = profile.get("steps_target")
        if steps_target and not profile.get("activity"):
            activity = STEPS_ACTIVITY.get(int(steps_target), 1.375)
        else:
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
            "fat_g": fat_g, "water_ml": int(weight * 35), "goal": goal,
            # The quiet targets (Intake Protocol §5): fiber scales with kcal,
            # sugar is a WHO ceiling, sodium a DGA cap.
            "fiber_g": round(kcal / 1000 * 14),
            "sugar_g_max": round(kcal * 0.10 / 4),
            "sodium_mg_max": 2300}
    if profile.get("steps_target"):
        try:
            plan["steps_target"] = int(profile["steps_target"])
        except (TypeError, ValueError):
            pass
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
        if k == "steps_target":
            try:
                v = int(v)
            except (TypeError, ValueError):
                continue
            if not (2000 <= v <= 40000):
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


def save_profile_and_plan(db, user_id: str, incoming: dict,
                          kcal_override: int | None = None,
                          water_override: int | None = None) -> dict | None:
    """Merge sanitized fields into the profile fact, recompute the plan fact.
    Overrides let the person nudge the suggestion (Cal Neo's steppers) —
    macros re-derive from the overridden budget so the split stays honest."""
    from datetime import datetime, timezone

    from .substrate import read_facts, write_fact

    clean = sanitize_profile(incoming)
    facts = {f.key: f.value for f in read_facts(db, user_id=user_id,
                                                domains=["nutrition"], limit=30)}
    if not clean and not facts.get("profile"):
        return None  # nothing to merge and nothing to recompute from
    profile = {**facts.get("profile", {}), **clean}
    write_fact(db, user_id=user_id, domain="nutrition", key="profile",
               value=profile, confidence=1.0, source_agent="nutrition")
    plan = compute_plan(profile)
    if plan is None:
        return None
    if kcal_override and 800 <= int(kcal_override) <= 6000:
        plan["kcal"] = int(kcal_override)
        plan["fat_g"] = round(plan["kcal"] * 0.25 / 9)
        plan["carbs_g"] = max(round((plan["kcal"] - plan["protein_g"] * 4
                                     - plan["fat_g"] * 9) / 4), 0)
        plan["fiber_g"] = round(plan["kcal"] / 1000 * 14)
        plan["sugar_g_max"] = round(plan["kcal"] * 0.10 / 4)
    if water_override and 1000 <= int(water_override) <= 6000:
        plan["water_ml"] = int(water_override)
    old_plan = facts.get("plan", {})
    plan["started"] = old_plan.get("started") or datetime.now(timezone.utc).date().isoformat()
    write_fact(db, user_id=user_id, domain="nutrition", key="plan",
               value=plan, confidence=1.0, source_agent="nutrition")
    return plan


def health_score(today: dict, plan: dict) -> tuple[int, str]:
    """Deterministic 0-100 balance score against the plan's targets.
    No meals logged yet = no score (returns -1)."""
    if not plan or not today.get("meals"):
        return -1, "Log a meal and I'll score the day."
    score = 100.0
    notes = []
    kcal, target = today.get("kcal", 0), plan["kcal"]
    if kcal > target:
        over = (kcal - target) / target
        score -= min(over * 100, 30)
        notes.append("over budget")
    p_ratio = (today.get("protein_g", 0) or 0) / max(plan["protein_g"], 1)
    day_frac = min(kcal / max(target, 1), 1.0)
    if day_frac > 0.5 and p_ratio < day_frac * 0.6:
        score -= 15
        notes.append("light on protein")
    fiber_target = plan.get("fiber_g", 30)
    if day_frac > 0.5 and (today.get("fiber_g", 0) or 0) < fiber_target * day_frac * 0.5:
        score -= 10
        notes.append("low fiber")
    sugar_max = plan.get("sugar_g_max", 50)
    if (today.get("sugar_g", 0) or 0) > sugar_max:
        score -= 15
        notes.append("sugar past the WHO line")
    sodium_max = plan.get("sodium_mg_max", 2300)
    if (today.get("sodium_mg", 0) or 0) > sodium_max:
        score -= 15
        notes.append("salty day")
    score = max(int(round(score)), 5)
    n = len(today["meals"])
    note = (f"Reflects balance across today's {n} meal{'s' if n != 1 else ''}"
            + (" — " + ", ".join(notes) if notes else " — on track") + ".")
    return score, note
