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
    if resp.status_code != 200:
        return b""  # quota or transient failure: the orb shows text, silently
    cached.write_bytes(resp.content)
    return resp.content


def tts_timed(text: str) -> dict:
    """Text -> {audio_b64, words:[{w, t}]} with word-start times in seconds,
    derived from ElevenLabs character timestamps. Cached like tts()."""
    import base64
    import json as _json

    settings = get_settings()
    if not settings.elevenlabs_api_key:
        return {"audio_b64": "", "words": []}

    cache_dir = Path(settings.media_dir) / "tts-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(f"timed:{settings.nano_voice_id}:{text}".encode()).hexdigest()[:32]
    cached = cache_dir / f"{key}.json"
    if cached.exists():
        try:
            return _json.loads(cached.read_text())
        except _json.JSONDecodeError:
            pass

    resp = httpx.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{settings.nano_voice_id}/with-timestamps",
        headers={"xi-api-key": settings.elevenlabs_api_key},
        json={
            "text": text,
            "model_id": settings.elevenlabs_model,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "style": 0.25},
        },
        timeout=90,
    )
    if resp.status_code != 200:
        return {"audio_b64": "", "words": []}
    data = resp.json()
    chars = (data.get("alignment") or {}).get("characters", [])
    starts = (data.get("alignment") or {}).get("character_start_times_seconds", [])
    words, buf, t0 = [], "", None
    for ch, st in zip(chars, starts):
        if ch.isspace():
            if buf:
                words.append({"w": buf, "t": round(t0 or 0.0, 3)})
                buf, t0 = "", None
        else:
            if not buf:
                t0 = st
            buf += ch
    if buf:
        words.append({"w": buf, "t": round(t0 or 0.0, 3)})
    out = {"audio_b64": data.get("audio_base64", ""), "words": words}
    cached.write_text(_json.dumps(out))
    return out
