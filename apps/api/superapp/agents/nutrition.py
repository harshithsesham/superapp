"""Nutrition agent (Phase 1) — the first real vertical.

think() handles two trigger kinds:
- meal_photo / meal_text: multimodal structured-output estimate for one meal,
  written to the nutrition_meals twin (via the substrate helper). In stub mode
  (no API key) a deterministic low-confidence estimate keeps the spine testable.
- scheduled / user_refresh: the daily summary — one insight over today's meals
  vs the target fact. Cron runs go through the Batches API at 50% price.

render() is a pure projection: today's totals vs target, the meal list, the
latest summary insight, and the last photo.
"""
import base64
import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .. import storage
from ..llm.provider import LLMProvider
from ..sdui.blocks import (
    Action, ActionRow, Bar, BarChart, DayStrip, ImageCard, InsightCard,
    ListBlock, ListItem, Meter, MeterRow, RingHero, Screen, Section, Stat,
    StatRow, StripDay, TextBlock,
)
from ..substrate import ContextSlice, update_meal_estimate
from .base import EventWrite, FactWrite, ThinkResult, register_agent

# Frozen system prompts — provider caches these as the prefix; never interpolate
# per-run values here (they go in the prompt, after the cache breakpoint).
ESTIMATE_SYSTEM = (
    "You are the nutrition agent of a personal health app. Given a meal (photo "
    "and/or text description), identify the meal and estimate calories and "
    "macronutrients for the portion shown. Be realistic about portion sizes; "
    "when uncertain, estimate the middle of the plausible range and lower your "
    "confidence. confidence is 0..1."
)
SUMMARY_SYSTEM = (
    "You are the nutrition agent of a personal health app. Write one short, "
    "concrete daily summary insight (2-3 sentences) about what the user ate "
    "today versus their calorie target. Mention the most impactful meal. No "
    "greetings, no bullet points, no moralizing."
)

MEAL_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "kcal": {"type": "integer"},
        "protein_g": {"type": "number"},
        "carbs_g": {"type": "number"},
        "fat_g": {"type": "number"},
        "confidence": {"type": "number"},
    },
    "required": ["description", "kcal", "protein_g", "carbs_g", "fat_g", "confidence"],
    "additionalProperties": False,
}

STUB_ESTIMATE = {"kcal": 500, "protein_g": 20.0, "carbs_g": 50.0, "fat_g": 20.0, "confidence": 0.2}


def _plan(context: ContextSlice) -> dict | None:
    fact = next((f for f in context.facts
                 if f["domain"] == "nutrition" and f["key"] == "plan"), None)
    return fact["value"] if fact else None


def _target_kcal(context: ContextSlice) -> int | None:
    fact = next(
        (f for f in context.facts if f["domain"] == "nutrition" and f["key"] == "daily_target"),
        None,
    )
    return fact["value"].get("kcal") if fact else None


def _estimate_meal(db: Session, context: ContextSlice, trigger: dict) -> ThinkResult:
    provider = LLMProvider()
    images = None
    if trigger.get("photo_id"):
        data, media_type = storage.read_photo(trigger["photo_id"])
        images = [(media_type, base64.standard_b64encode(data).decode())]

    described = trigger.get("description", "")
    resp = provider.complete(
        db,
        user_id=context.user_id,
        agent="nutrition",
        task="estimate",
        system=ESTIMATE_SYSTEM,
        prompt=f"Meal description from the user: {described!r}" if described else "Estimate the meal in the photo.",
        images=images,
        schema=MEAL_SCHEMA,
    )

    estimate = dict(STUB_ESTIMATE, description=described or "Meal (stub estimate)")
    if not resp.stubbed and not resp.refused:
        try:
            parsed = json.loads(resp.text)
            if all(k in parsed for k in MEAL_SCHEMA["required"]):
                estimate = parsed
        except (json.JSONDecodeError, TypeError):
            pass  # keep the fallback; the llm_call event preserves what happened

    meal = update_meal_estimate(
        db,
        user_id=context.user_id,
        meal_id=trigger["meal_id"],
        description=str(estimate["description"])[:500],
        kcal=int(estimate["kcal"]),
        protein_g=float(estimate["protein_g"]),
        carbs_g=float(estimate["carbs_g"]),
        fat_g=float(estimate["fat_g"]),
        confidence=float(estimate["confidence"]),
    )
    return ThinkResult(
        event_writes=[
            EventWrite(
                type="meal_estimated",
                domain="nutrition",
                payload={"meal_id": meal.id, "kcal": meal.kcal, "confidence": meal.confidence},
            )
        ]
    )


def _daily_summary(db: Session, context: ContextSlice, trigger: dict) -> ThinkResult:
    today = context.domain_data.get("nutrition", {}).get("today", {})
    if not today.get("meals"):
        return ThinkResult()  # nothing eaten, nothing to say

    provider = LLMProvider()
    target = _target_kcal(context)
    prompt = json.dumps({"today": today, "target_kcal": target}, sort_keys=True)

    if trigger.get("kind") == "scheduled":
        # Cron path: nobody is waiting — 50% off via the Batches API.
        resp = provider.complete_batch(
            db, user_id=context.user_id, agent="nutrition", task="daily_summary",
            system=SUMMARY_SYSTEM, prompts={"summary": prompt},
        )["summary"]
    else:
        resp = provider.complete(
            db, user_id=context.user_id, agent="nutrition", task="daily_summary",
            system=SUMMARY_SYSTEM, prompt=prompt,
        )

    if resp is None or resp.refused:
        return ThinkResult()
    text = resp.text
    if resp.stubbed:
        kcal, n = today["kcal"], len(today["meals"])
        vs = f" against a {target} kcal target" if target else ""
        text = f"{kcal} kcal across {n} meal(s) today{vs}."

    return ThinkResult(
        fact_writes=[
            FactWrite(
                domain="nutrition",
                key="last_summary",
                value={"date": today.get("date"), "kcal": today.get("kcal"), "text": text[:600]},
                confidence=0.9,
            )
        ]
    )


FIX_SYSTEM = (
    "You are the nutrition agent. The user corrected your estimate of a meal. "
    "Re-estimate the SAME portion with their correction applied. Keep what "
    "they didn't dispute close to the original. confidence is 0..1."
)


def fix_meal(db: Session, *, user_id: str, meal_id: str, note: str,
             original: dict) -> dict:
    provider = LLMProvider()
    resp = provider.complete(
        db, user_id=user_id, agent="nutrition", task="estimate",
        system=FIX_SYSTEM,
        prompt=json.dumps({"original_estimate": original, "user_correction": note},
                          sort_keys=True),
        schema=MEAL_SCHEMA,
    )
    estimate = dict(original, confidence=max(original.get("confidence", 0.5), 0.5))
    if not resp.stubbed and not resp.refused:
        try:
            parsed = json.loads(resp.text)
            if all(k in parsed for k in MEAL_SCHEMA["required"]):
                estimate = parsed
        except (json.JSONDecodeError, TypeError):
            pass
    elif resp.stubbed:
        estimate = dict(original, description=f"{original.get('description', 'Meal')} (fixed)",
                        confidence=0.6)
    update_meal_estimate(
        db, user_id=user_id, meal_id=meal_id,
        description=str(estimate["description"])[:500],
        kcal=int(estimate["kcal"]),
        protein_g=float(estimate["protein_g"]),
        carbs_g=float(estimate["carbs_g"]),
        fat_g=float(estimate["fat_g"]),
        confidence=float(estimate["confidence"]),
    )
    return estimate


def nutrition_think(db: Session, *, trigger: dict, context: ContextSlice, run_id: str) -> ThinkResult:
    if trigger.get("kind") in ("meal_photo", "meal_text"):
        return _estimate_meal(db, context, trigger)
    return _daily_summary(db, context, trigger)


def nutrition_render(context: ContextSlice) -> Screen:
    data = context.domain_data.get("nutrition", {})
    today = data.get("today", {"kcal": 0, "meals": []})
    target = _target_kcal(context)
    summary = next(
        (f for f in context.facts if f["domain"] == "nutrition" and f["key"] == "last_summary"),
        None,
    )

    plan = _plan(context)
    blocks: list = []

    week = data.get("week", [])
    streak = data.get("streak_days", 0)
    if week:
        blocks.append(DayStrip(
            days=[StripDay(letter=d["day"][0], num=d["date"][-2:].lstrip("0"),
                           logged=d["meals"] > 0,
                           today=d["date"] == today.get("date"))
                  for d in week],
            chip=f"DAY {streak}" if streak >= 1 else None,
        ))

    if plan:
        # The Cal Neo hero: serif number, mono label, macro chips, eaten ring.
        left = max(plan["kcal"] - today["kcal"], 0)
        eaten_pct = min(today["kcal"] / plan["kcal"], 1.0) if plan["kcal"] else 0.0
        p_left = max(plan["protein_g"] - (today.get("protein_g", 0) or 0), 0)
        c_left = max(plan["carbs_g"] - (today.get("carbs_g", 0) or 0), 0)
        f_left = max(plan["fat_g"] - (today.get("fat_g", 0) or 0), 0)
        blocks.append(RingHero(
            big=f"{left:,}",
            label=f"KCAL LEFT OF {plan['kcal']:,}",
            chips=[f"P {p_left:g}g", f"C {c_left:g}g", f"F {f_left:g}g"],
            pct=round(eaten_pct, 3),
            pct_label="EATEN",
        ))
        activity = data.get("activity")
        if activity and activity.get("steps"):
            blocks.append(TextBlock(
                text=f"{activity['steps']:,} steps · {activity.get('active_kcal', 0):,} kcal burned",
                variant="caption"))
    else:
        blocks.append(TextBlock(text="Let's build your plan.", variant="title"))
        blocks.append(TextBlock(
            text="One minute of talking: your weight, height, target weight, and "
                 "how you train — I'll compute your daily calories and macros "
                 "from it. Nothing is guessed.",
            variant="body"))
        blocks.append(ActionRow(actions=[
            Action(id="nutrition.setup", label="Set up with Nano")
        ]))

    blocks.append(TextBlock(text=f"TODAY · {len(today['meals'])} LOGGED", variant="caption"))
    if today["meals"]:
        blocks.append(
            ListBlock(
                items=[
                    ListItem(
                        id=m["id"],
                        title=(m["description"] or m["source"])[:60],
                        tile=((m["description"] or "M")[0] or "M").upper(),
                        subtitle=f"{m['kcal'] or '…'} kcal  P {m['protein_g'] or 0:g}g  "
                                 f"C {m['carbs_g'] or 0:g}g  F {m['fat_g'] or 0:g}g",
                        trailing=m["logged_at"][11:16],
                        detail=(f"{m['description']}\n\n{m['kcal'] or '?'} kcal — "
                                f"protein {m['protein_g'] or 0:g}g, carbs {m['carbs_g'] or 0:g}g, "
                                f"fat {m['fat_g'] or 0:g}g."),
                        fixable_id=m["id"],
                    )
                    for m in today["meals"]
                ]
            )
        )
        last_photo = next((m["photo_id"] for m in today["meals"] if m["photo_id"]), None)
        if last_photo:
            blocks.append(ImageCard(image_url=f"/v1/media/{last_photo}", title="Latest plate"))
    else:
        blocks.append(TextBlock(text="Nothing yet. Snap your first plate.", variant="caption"))
    blocks.append(ActionRow(actions=[
        Action(id="nutrition.photo", label="Snap the plate"),
        Action(id="nutrition.water", label="＋ Water", style="secondary"),
    ]))
    if plan and plan.get("water_ml"):
        blocks.append(MeterRow(meters=[
            Meter(label="WATER", value=today.get("water_ml", 0),
                  max=plan["water_ml"], unit="ml", tone="mint"),
        ]))

    chart_week = week[-7:]
    if any(d["kcal"] for d in chart_week):
        avg_days = [d for d in chart_week if d["kcal"]]
        avg = int(sum(d["kcal"] for d in avg_days) / max(len(avg_days), 1))
        blocks.append(TextBlock(text=f"THIS WEEK · {avg:,} KCAL DAILY AVERAGE",
                                variant="caption"))
        blocks.append(BarChart(
            bars=[Bar(label=d["day"], value=d["kcal"],
                      accent=(d["date"] == today.get("date"))) for d in chart_week],
            target=float(plan["kcal"]) if plan else None,
        ))

    if summary:
        blocks.append(
            InsightCard(
                id="nutrition-summary",
                agent="nutrition",
                title=f"Daily summary — {summary['value'].get('date', '')}",
                body=summary["value"].get("text", ""),
                emphasis="default",
            )
        )

    return Screen(title="Nutrition", theme="dark", sections=[Section(title=None, blocks=blocks)])


register_agent("nutrition", render=nutrition_render, think=nutrition_think)
