"""Meal photo storage — local disk for dev (roadmap: R2 later; swap this module).

Files are stored as {uuid}.{ext} under settings.media_dir. The id doubles as the
public path segment (/v1/media/{photo_id}), so it is validated strictly to make
traversal impossible.
"""
import re
import uuid
from pathlib import Path

from .config import get_settings

_EXT_BY_MIME = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/heic": "heic"}
_MIME_BY_EXT = {v: k for k, v in _EXT_BY_MIME.items()}
_PHOTO_ID_RE = re.compile(r"^[0-9a-f]{32}\.(jpg|png|webp|heic)$")

MAX_PHOTO_BYTES = 15 * 1024 * 1024


def save_photo(data: bytes, content_type: str) -> str:
    ext = _EXT_BY_MIME.get(content_type)
    if ext is None:
        raise ValueError(f"Unsupported photo type {content_type!r}")
    if len(data) > MAX_PHOTO_BYTES:
        raise ValueError("Photo too large")
    photo_id = f"{uuid.uuid4().hex}.{ext}"
    root = Path(get_settings().media_dir)
    root.mkdir(parents=True, exist_ok=True)
    (root / photo_id).write_bytes(data)
    return photo_id


def read_photo(photo_id: str) -> tuple[bytes, str]:
    """Returns (bytes, media_type). Raises FileNotFoundError / ValueError."""
    if not _PHOTO_ID_RE.match(photo_id):
        raise ValueError(f"Invalid photo id {photo_id!r}")
    path = Path(get_settings().media_dir) / photo_id
    return path.read_bytes(), _MIME_BY_EXT[photo_id.rsplit(".", 1)[1]]
