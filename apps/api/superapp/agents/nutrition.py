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
    ImageCard, InsightCard, ListBlock, ListItem, Meter, MeterRow, Screen,
    Section, Stat, StatRow, TextBlock,
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

    if plan:
        # The Cal AI hero, in Nano's voice: what's LEFT, counted honestly.
        left = max(plan["kcal"] - today["kcal"], 0)
        blocks.append(TextBlock(text=f"{left:,} calories left.", variant="title"))
        blocks.append(TextBlock(
            text=f"{today['kcal']:,} of {plan['kcal']:,} eaten · plan: {plan.get('goal', 'maintain')}",
            variant="caption"))
        blocks.append(MeterRow(meters=[
            Meter(label="PROTEIN", value=today.get("protein_g", 0) or 0,
                  max=plan["protein_g"], tone="rose"),
            Meter(label="CARBS", value=today.get("carbs_g", 0) or 0,
                  max=plan["carbs_g"], tone="amber"),
            Meter(label="FAT", value=today.get("fat_g", 0) or 0,
                  max=plan["fat_g"], tone="lavender"),
        ]))
    else:
        stats = [Stat(label="Today", value=str(today["kcal"]), unit="kcal")]
        if target:
            stats.append(Stat(label="Target", value=str(target), unit="kcal"))
            stats.append(Stat(label="Left", value=str(max(target - today["kcal"], 0)), unit="kcal"))
        blocks.append(StatRow(stats=stats))
        blocks.append(TextBlock(
            text="Tap the orb — one minute of talking and I'll build your daily plan.",
            variant="caption"))

    if today["meals"]:
        blocks.append(
            ListBlock(
                items=[
                    ListItem(
                        id=m["id"],
                        title=m["description"] or m["source"],
                        subtitle=f"{m['protein_g'] or 0:g}g P · {m['carbs_g'] or 0:g}g C · {m['fat_g'] or 0:g}g F",
                        trailing=f"{m['kcal'] or '…'} kcal",
                        detail=(f"{m['description']}\n\n{m['kcal'] or '?'} kcal — "
                                f"protein {m['protein_g'] or 0:g}g, carbs {m['carbs_g'] or 0:g}g, "
                                f"fat {m['fat_g'] or 0:g}g."),
                    )
                    for m in today["meals"]
                ]
            )
        )
        last_photo = next((m["photo_id"] for m in today["meals"] if m["photo_id"]), None)
        if last_photo:
            blocks.append(ImageCard(image_url=f"/v1/media/{last_photo}", title="Latest meal"))
    else:
        blocks.append(TextBlock(text="No meals logged today. Snap your next one.", variant="caption"))

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

    return Screen(title="Nutrition", sections=[Section(title="Today", blocks=blocks)])


register_agent("nutrition", render=nutrition_render, think=nutrition_think)
