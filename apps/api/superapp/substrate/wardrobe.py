"""Wardrobe domain twin operations — the only module touching the wardrobe tables."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import OutfitSuggestion, WardrobeGarment


def create_garment(db: Session, *, user_id: str, source: str, photo_id: str | None) -> WardrobeGarment:
    garment = WardrobeGarment(user_id=user_id, source=source, photo_id=photo_id)
    db.add(garment)
    db.flush()
    return garment


def update_garment_attrs(db: Session, *, user_id: str, garment_id: str, attrs: dict) -> WardrobeGarment:
    garment = db.get(WardrobeGarment, garment_id)
    if garment is None or garment.user_id != user_id:
        raise ValueError(f"No garment {garment_id!r} for user")
    for field in ("name", "brand", "type", "primary_color", "secondary_color",
                  "pattern", "material", "formality"):
        if attrs.get(field) is not None:
            setattr(garment, field, attrs[field])
    if attrs.get("seasons") is not None:
        garment.seasons = {"seasons": attrs["seasons"]}
    if attrs.get("confidence") is not None:
        garment.confidence = float(attrs["confidence"])
    db.flush()
    return garment


def garments(db: Session, user_id: str, limit: int = 200) -> list[WardrobeGarment]:
    return list(db.scalars(
        select(WardrobeGarment)
        .where(WardrobeGarment.user_id == user_id)
        .order_by(WardrobeGarment.created_at.desc())
        .limit(limit)
    ))


def save_suggestions(db: Session, *, user_id: str, day: str, suggestions: list[dict]) -> list[OutfitSuggestion]:
    # Regenerating for the same day replaces that day's set.
    for old in db.scalars(select(OutfitSuggestion).where(
            OutfitSuggestion.user_id == user_id, OutfitSuggestion.day == day)):
        db.delete(old)
    rows = []
    for s in suggestions:
        row = OutfitSuggestion(
            user_id=user_id, day=day, title=s.get("title", ""), occasion=s.get("occasion", ""),
            rationale=s.get("rationale", ""), items={"garment_ids": s.get("garment_ids", [])},
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def suggestions_for_day(db: Session, *, user_id: str, day: str) -> list[OutfitSuggestion]:
    return list(db.scalars(select(OutfitSuggestion).where(
        OutfitSuggestion.user_id == user_id, OutfitSuggestion.day == day
    ).order_by(OutfitSuggestion.created_at)))


def recent_suggestions(db: Session, *, user_id: str, days: int = 14) -> list[OutfitSuggestion]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    return list(db.scalars(select(OutfitSuggestion).where(
        OutfitSuggestion.user_id == user_id, OutfitSuggestion.created_at >= since
    )))


def wardrobe_context(db: Session, user_id: str) -> dict:
    """The wardrobe slice of ContextSlice.domain_data."""
    rows = garments(db, user_id)
    today = datetime.now(timezone.utc).date().isoformat()

    def g_row(g: WardrobeGarment) -> dict:
        return {
            "id": g.id, "name": g.name, "brand": g.brand, "type": g.type,
            "primary_color": g.primary_color, "secondary_color": g.secondary_color,
            "pattern": g.pattern, "material": g.material, "formality": g.formality,
            "seasons": g.seasons.get("seasons", []), "photo_id": g.photo_id,
        }

    suggested_recently = {
        gid for s in recent_suggestions(db, user_id=user_id)
        for gid in s.items.get("garment_ids", [])
    }
    by_type: dict[str, int] = {}
    for g in rows:
        by_type[g.type] = by_type.get(g.type, 0) + 1

    return {
        "garments": [g_row(g) for g in rows],
        "counts_by_type": by_type,
        "underused_ids": [g.id for g in rows if g.id not in suggested_recently][:10],
        "todays_outfits": [
            {"id": s.id, "title": s.title, "occasion": s.occasion, "rationale": s.rationale,
             "garment_ids": s.items.get("garment_ids", [])}
            for s in suggestions_for_day(db, user_id=user_id, day=today)
        ],
    }
