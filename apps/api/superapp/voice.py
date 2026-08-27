"""Nano's voice: ElevenLabs text-to-speech. Stub mode (no key) returns empty
audio so the interview runs silently in dev — the transcript is the asset."""
import hashlib
from pathlib import Path

import httpx

from .config import get_settings


def tts(text: str) -> bytes:
    """Text -> mp3 bytes. Cached on disk by content hash (questions repeat
    across users; Nano's openers cost credits once, ever)."""
    settings = get_settings()
    if not settings.elevenlabs_api_key:
        return b""

    cache_dir = Path(settings.media_dir) / "tts-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(f"{settings.nano_voice_id}:{text}".encode()).hexdigest()[:32]
    cached = cache_dir / f"{key}.mp3"
    if cached.exists():
        return cached.read_bytes()

    resp = httpx.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{settings.nano_voice_id}",
        headers={"xi-api-key": settings.elevenlabs_api_key},
        json={
            "text": text,
            "model_id": settings.elevenlabs_model,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "style": 0.25},
        },
        timeout=60,
    )
    resp.raise_for_status()
    cached.write_bytes(resp.content)
    return resp.content
