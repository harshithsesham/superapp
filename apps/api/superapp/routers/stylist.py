"""Stylist vertical endpoints (Phase 4). Garment upload mirrors the nutrition
photo flow: synchronous extract-and-return-screen."""
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session

from .. import storage
from ..agents.base import render_screen, run_think
from ..auth import current_user_id
from ..db import get_db
from ..substrate import append_event
from ..substrate.wardrobe import create_garment

router = APIRouter(prefix="/v1", tags=["stylist"])


@router.post("/wardrobe/photo")
async def add_garment_photo(
    photo: UploadFile, user_id: str = Depends(current_user_id), db: Session = Depends(get_db)
):
    data = await photo.read()
    try:
        photo_id = storage.save_photo(data, photo.content_type or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    garment = create_garment(db, user_id=user_id, source="photo_upload", photo_id=photo_id)
    append_event(db, user_id=user_id, type="garment_added", agent="stylist", domain="wardrobe",
                 payload={"garment_id": garment.id})
    run_think(db, agent="stylist", user_id=user_id,
              trigger={"kind": "garment_photo", "garment_id": garment.id, "photo_id": photo_id})
    return render_screen(db, agent="stylist", user_id=user_id).model_dump()
