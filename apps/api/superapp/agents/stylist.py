"""Stylist agent (Phase 4, ported from styleagent) — outfits from owned clothes.

think() trigger kinds:
- garment_photo: vision pass extracts attributes into the wardrobe twin.
- scheduled / user_refresh: (1) if enough new feedback accumulated, distill the
  like/dislike history into style-memory facts (styleagent's distillation,
  restructured for user_facts); (2) generate today's 3 outfits from the
  wardrobe + weather + style facts. Cron runs batch at 50%.

render() is the visual one: today's outfit cards, the closet grid, weather line.
Scope is cross-domain (wardrobe + goals + nutrition + finance) — the substrate
payoff arrives free.
"""
import base64
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .. import storage
from ..llm.provider import LLMProvider
from ..sdui.blocks import (
    GridImage, ImageGrid, InsightCard, OutfitCard, OutfitItem, Screen, Section, Stat, StatRow, TextBlock,
)
from ..stylist.weather import todays_weather
from ..substrate import ContextSlice
from ..substrate.events import recent_events
from ..substrate.wardrobe import save_suggestions, suggestions_for_day, update_garment_attrs
from .base import EventWrite, FactWrite, ThinkResult, register_agent

GARMENT_SYSTEM = (
    "You are the stylist agent of a personal wardrobe app. Given a photo of one "
    "garment, extract its attributes. type is one of: top, bottom, dress, "
    "outerwear, shoes, accessory. formality is one of: casual, smart_casual, "
    "business, formal. seasons is a subset of: spring, summer, fall, winter. "
    "name is a short human label like 'Navy oxford shirt'. confidence 0..1."
)
GARMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "type": {"type": "string", "enum": ["top", "bottom", "dress", "outerwear", "shoes", "accessory"]},
        "primary_color": {"type": "string"},
        "secondary_color": {"type": ["string", "null"]},
        "pattern": {"type": "string"},
        "material": {"type": ["string", "null"]},
        "formality": {"type": "string", "enum": ["casual", "smart_casual", "business", "formal"]},
        "seasons": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
    },
    "required": ["name", "type", "primary_color", "secondary_color", "pattern",
                 "material", "formality", "seasons", "confidence"],
    "additionalProperties": False,
}

OUTFIT_SYSTEM = (
    "You are a personal stylist. From the user's OWNED garments only, compose 3 "
    "distinct outfits for today. Respect the weather, the user's style profile "
    "and avoidances, and basic color harmony (complementary or analogous "
    "palettes; at most one statement piece per outfit). Prefer underused "
    "garments when they fit. Each outfit: a short evocative title, the "
    "occasion, garment_ids drawn ONLY from the provided ids, and a 1-2 sentence "
    "rationale mentioning the weather or the user's style."
)
OUTFIT_SCHEMA = {
    "type": "object",
    "properties": {
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "occasion": {"type": "string"},
                    "rationale": {"type": "string"},
                    "garment_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "occasion", "rationale", "garment_ids"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["suggestions"],
    "additionalProperties": False,
}

# Ported from styleagent's style-memory distillation prompt, reshaped so every
# field is a compact string (facts hold beliefs — small, no top-level lists).
DISTILL_SYSTEM = (
    "You are a fashion psychologist analyzing a user's outfit feedback history. "
    "Extract a structured style profile. Be specific, never generic. "
    "aesthetic_identity: 1-2 sentences of core style DNA. style_affinities: up "
    "to 5 styles with 0-1 scores, e.g. 'minimal (0.9), streetwear (0.4)'. "
    "avoidances: only patterns appearing in 3+ dislikes, e.g. 'no neon; avoid "
    "skinny fits'. color_preferences: liked colors, avoided colors, and combos "
    "like 'navy+white'. formality_floor/ceiling: the comfortable range."
)
DISTILL_SCHEMA = {
    "type": "object",
    "properties": {
        "aesthetic_identity": {"type": "string"},
        "style_affinities": {"type": "string"},
        "avoidances": {"type": "string"},
        "colors_preferred": {"type": "string"},
        "colors_avoided": {"type": "string"},
        "color_combos": {"type": "string"},
        "formality_floor": {"type": "string"},
        "formality_ceiling": {"type": "string"},
    },
    "required": ["aesthetic_identity", "style_affinities", "avoidances", "colors_preferred",
                 "colors_avoided", "color_combos", "formality_floor", "formality_ceiling"],
    "additionalProperties": False,
}

MIN_FEEDBACK_FIRST = 3     # styleagent: first distillation after 3 feedbacks
DISTILL_EVERY = 6          # re-distill every 6 new feedbacks
FEEDBACK_TYPES = ["outfit_liked", "outfit_rejected"]


def _fact(context: ContextSlice, key: str) -> dict | None:
    f = next((f for f in context.facts if f["domain"] == "wardrobe" and f["key"] == key), None)
    return f["value"] if f else None


def _extract_garment(db: Session, context: ContextSlice, trigger: dict) -> ThinkResult:
    provider = LLMProvider()
    data, media_type = storage.read_photo(trigger["photo_id"])
    resp = provider.complete(
        db, user_id=context.user_id, agent="stylist", task="garment_extraction",
        system=GARMENT_SYSTEM, prompt="Extract this garment's attributes.",
        images=[(media_type, base64.standard_b64encode(data).decode())],
        schema=GARMENT_SCHEMA,
    )

    # Stub fallback: cycle types so outfits can still form offline.
    n = len(context.domain_data.get("wardrobe", {}).get("garments", []))
    attrs = {
        "name": f"Garment #{n + 1} (stub)", "type": ["top", "bottom", "shoes"][n % 3],
        "primary_color": ["navy", "ecru", "charcoal"][n % 3], "pattern": "solid",
        "formality": "casual", "seasons": ["spring", "summer", "fall", "winter"],
        "confidence": 0.2,
    }
    if not resp.stubbed and not resp.refused:
        try:
            parsed = json.loads(resp.text)
            if all(k in parsed for k in ("name", "type", "primary_color")):
                attrs = parsed
        except json.JSONDecodeError:
            pass

    garment = update_garment_attrs(db, user_id=context.user_id,
                                   garment_id=trigger["garment_id"], attrs=attrs)
    return ThinkResult(event_writes=[EventWrite(
        type="garment_extracted", domain="wardrobe",
        payload={"garment_id": garment.id, "type": garment.type, "name": garment.name},
    )])


def _feedback_corpus(db: Session, context: ContextSlice) -> list[dict]:
    """Join feedback events to what was actually in those outfits."""
    from ..models import OutfitSuggestion

    garment_by_id = {g["id"]: g for g in context.domain_data.get("wardrobe", {}).get("garments", [])}
    corpus = []
    for e in recent_events(db, user_id=context.user_id, limit=500, types=FEEDBACK_TYPES):
        outfit = db.get(OutfitSuggestion, e.payload.get("target_id", ""))
        if outfit is None:
            continue
        corpus.append({
            "action": "like" if e.type == "outfit_liked" else "dislike",
            "title": outfit.title,
            "garments": [
                {k: garment_by_id[gid].get(k) for k in ("type", "primary_color", "pattern", "formality")}
                for gid in outfit.items.get("garment_ids", []) if gid in garment_by_id
            ],
        })
    return corpus


def _maybe_distill(db: Session, context: ContextSlice, result: ThinkResult) -> None:
    feedback = _feedback_corpus(db, context)
    meta = _fact(context, "last_distillation") or {"feedback_count": 0}
    new = len(feedback) - meta["feedback_count"]
    first = meta["feedback_count"] == 0
    if not ((first and len(feedback) >= MIN_FEEDBACK_FIRST) or new >= DISTILL_EVERY):
        return

    provider = LLMProvider()
    resp = provider.complete(
        db, user_id=context.user_id, agent="stylist", task="style_distillation",
        system=DISTILL_SYSTEM, prompt=json.dumps({"feedback": feedback}, sort_keys=True),
        schema=DISTILL_SCHEMA,
    )
    if resp.refused:
        return
    try:
        profile = json.loads(resp.text)
    except json.JSONDecodeError:
        return
    if resp.stubbed:
        likes = [f for f in feedback if f["action"] == "like"]
        top = likes[0]["garments"][0]["primary_color"] if likes and likes[0]["garments"] else "navy"
        profile = {"aesthetic_identity": f"Leans into {top}-anchored, easy silhouettes (stub profile).",
                   "style_affinities": "minimal (0.8)", "avoidances": "",
                   "colors_preferred": top, "colors_avoided": "", "color_combos": "",
                   "formality_floor": "casual", "formality_ceiling": "smart_casual"}

    result.fact_writes += [
        FactWrite(domain="wardrobe", key="style_profile", value={
            "aesthetic_identity": str(profile.get("aesthetic_identity", ""))[:280],
            "style_affinities": str(profile.get("style_affinities", ""))[:200],
            "avoidances": str(profile.get("avoidances", ""))[:200],
            "colors_preferred": str(profile.get("colors_preferred", ""))[:120],
            "colors_avoided": str(profile.get("colors_avoided", ""))[:120],
            "formality": f"{profile.get('formality_floor', '')}-{profile.get('formality_ceiling', '')}",
        }, confidence=0.85),
        FactWrite(domain="wardrobe", key="last_distillation", value={
            "at": datetime.now(timezone.utc).isoformat(), "feedback_count": len(feedback),
        }, confidence=1.0),
    ]
    result.event_writes.append(EventWrite(type="style_distilled", domain="wardrobe",
                                          payload={"feedback_count": len(feedback)}))


def _generate_outfits(db: Session, context: ContextSlice, trigger: dict, result: ThinkResult) -> None:
    wardrobe = context.domain_data.get("wardrobe", {})
    garments = wardrobe.get("garments", [])
    if len(garments) < 2:
        return

    weather = todays_weather()
    result.fact_writes.append(FactWrite(
        domain="wardrobe", key="weather", value=weather, confidence=1.0,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=3),
    ))

    provider = LLMProvider()
    prompt = json.dumps({
        "garments": [{k: g[k] for k in ("id", "name", "type", "primary_color", "secondary_color",
                                        "pattern", "formality", "seasons")} for g in garments],
        "underused_ids": wardrobe.get("underused_ids", []),
        "weather": weather,
        "style_profile": _fact(context, "style_profile"),
        "goals": [f["value"] for f in context.facts if f["domain"] == "goals"],
    }, sort_keys=True)

    if trigger.get("kind") == "scheduled":
        resp = provider.complete_batch(
            db, user_id=context.user_id, agent="stylist", task="outfits",
            system=OUTFIT_SYSTEM, prompts={"outfits": prompt},
        )["outfits"]
    else:
        resp = provider.complete(db, user_id=context.user_id, agent="stylist", task="outfits",
                                 system=OUTFIT_SYSTEM, prompt=prompt)
    if resp is None or resp.refused:
        return

    suggestions = None
    if not resp.stubbed:
        try:
            parsed = json.loads(resp.text)
            valid_ids = {g["id"] for g in garments}
            suggestions = [
                {**s, "garment_ids": [g for g in s["garment_ids"] if g in valid_ids]}
                for s in parsed.get("suggestions", [])[:3]
                if s.get("garment_ids")
            ] or None
        except json.JSONDecodeError:
            pass
    if suggestions is None:
        # Deterministic offline fallback: round-robin one garment per type.
        by_type: dict[str, list[dict]] = {}
        for g in garments:
            by_type.setdefault(g["type"], []).append(g)
        suggestions = []
        for i in range(min(3, max(len(v) for v in by_type.values()))):
            picks = [v[i % len(v)] for v in by_type.values()]
            suggestions.append({
                "title": f"Look {i + 1} (stub)", "occasion": "everyday",
                "rationale": f"{weather['condition'].title()}, {weather['high_c']}°C — "
                             "an easy match from your closet.",
                "garment_ids": [p["id"] for p in picks],
            })

    day = datetime.now(timezone.utc).date().isoformat()
    rows = save_suggestions(db, user_id=context.user_id, day=day, suggestions=suggestions)
    result.event_writes.append(EventWrite(type="outfits_generated", domain="wardrobe",
                                          payload={"day": day, "count": len(rows)}))


def stylist_think(db: Session, *, trigger: dict, context: ContextSlice, run_id: str) -> ThinkResult:
    if trigger.get("kind") == "garment_photo":
        return _extract_garment(db, context, trigger)
    result = ThinkResult()
    _maybe_distill(db, context, result)
    _generate_outfits(db, context, trigger, result)
    return result


def stylist_render(context: ContextSlice) -> Screen:
    wardrobe = context.domain_data.get("wardrobe", {})
    garments = wardrobe.get("garments", [])
    garment_by_id = {g["id"]: g for g in garments}
    sections: list[Section] = []

    if not garments:
        return Screen(title="Stylist", theme="dark", sections=[Section(title="Your closet", blocks=[
            TextBlock(text="Photograph a few garments to start getting outfits — "
                           "tops, bottoms, shoes.", variant="body"),
        ])])

    weather = _fact(context, "weather")
    outfit_blocks: list = []
    if weather:
        outfit_blocks.append(TextBlock(
            text=f"{weather['condition'].title()} · {weather['low_c']}–{weather['high_c']}°C · "
                 f"{weather['precip_prob']}% rain",
            variant="caption",
        ))
    for o in wardrobe.get("todays_outfits", []):
        outfit_blocks.append(OutfitCard(
            id=o["id"], agent="stylist", title=o["title"], occasion=o["occasion"] or None,
            rationale=o["rationale"],
            items=[
                OutfitItem(
                    garment_id=gid,
                    image_url=f"/v1/media/{garment_by_id[gid]['photo_id']}"
                    if garment_by_id.get(gid, {}).get("photo_id") else None,
                    label=garment_by_id.get(gid, {}).get("name") or "garment",
                )
                for gid in o["garment_ids"] if gid in garment_by_id
            ],
        ))
    if not wardrobe.get("todays_outfits"):
        outfit_blocks.append(TextBlock(text="Pull to refresh for today's looks.", variant="caption"))
    sections.append(Section(title="Today", blocks=outfit_blocks))

    profile = _fact(context, "style_profile")
    if profile and profile.get("aesthetic_identity"):
        sections.append(Section(title="Your style", blocks=[InsightCard(
            id="style-profile", agent="stylist", title="Style DNA",
            body=profile["aesthetic_identity"], emphasis="default",
        )]))

    closet_blocks: list = [StatRow(stats=[
        Stat(label="Garments", value=str(len(garments))),
        *[Stat(label=t.title() + "s", value=str(n))
          for t, n in list(wardrobe.get("counts_by_type", {}).items())[:2]],
    ])]
    grid_items = [
        GridImage(id=g["id"], image_url=f"/v1/media/{g['photo_id']}", label=g["name"] or g["type"])
        for g in garments[:24] if g.get("photo_id")
    ]
    if grid_items:
        closet_blocks.append(ImageGrid(items=grid_items, columns=3))
    sections.append(Section(title="Closet", blocks=closet_blocks))

    return Screen(title="Stylist", theme="dark", sections=sections)


register_agent("stylist", render=stylist_render, think=stylist_think)
