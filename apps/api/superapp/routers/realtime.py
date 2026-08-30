"""Realtime voice (ElevenLabs Agents, custom-LLM mode).

ElevenLabs runs the ears and the mouth — streaming ASR, turn detection,
barge-in, streamed TTS in Nano's voice. The BRAIN stays here: on every turn
their orchestrator POSTs the conversation to /v1/llm/chat/completions and we
stream back Opus with Nano's full context (inbox, memory, identity).

Actions ride the stream as a trailing tag the voice never speaks:
    <<action:{"type": "send_draft", "draft_id": "..."}>>
We strip it from the SSE text, execute server-side, and queue client-side
effects (navigation) for the app to collect on /v1/voice/pending-actions.
"""
import hmac
import json
import time
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..auth import current_user_id, resolve_token
from ..config import get_settings
from ..db import get_db
from ..memory import recall
from ..substrate import get_context
from ..substrate.events import append_event
from .voice import (CONVERSE_SYSTEM, _execute, _inbox_for_voice,
                    _nutrition_for_voice, _tasks_for_voice)

router = APIRouter(tags=["realtime"])

REALTIME_SYSTEM = (
    CONVERSE_SYSTEM
    + "\nREALTIME MODE: you are in a live spoken conversation — replies flow "
    "straight to the person's ear. Keep them short and alive; one thought at "
    "a time; it's fine to be interrupted. Output PLAIN SPOKEN TEXT ONLY — no "
    "JSON, no markdown. To act, append EXACTLY ONE tag at the very END of "
    "your reply, on its own: "
    '<<action:{"type":"open_screen","screen":"inbox"}>> — types: open_screen '
    "(screen), refresh_inbox, draft_reply (message_id, reply_body, draft_id "
    "if editing), send_draft (draft_id), send_new_email (to_addr, subject, "
    "reply_body), set_nutrition (profile_json), log_water (reply_body=ml), research_task (reply_body=instruction), connect_site (reply_body=site), start_interview. The tag is "
    "silent; everything before it "
    "is spoken. Same rules as ever: send only on an explicit yes, never "
    "invent addresses or facts."
)

# Client-side effects awaiting pickup (single-worker deployment; best-effort).
_pending: dict[str, list[dict]] = {}


def _queue_client_action(user_id: str, action: dict) -> None:
    _pending.setdefault(user_id, []).append(action)


@router.get("/v1/voice/pending-actions")
def pending_actions(user_id: str = Depends(current_user_id)):
    return {"actions": _pending.pop(user_id, [])}


@router.get("/v1/voice/realtime-token")
def realtime_token(user_id: str = Depends(current_user_id)):
    """Mint a conversation token for the private agent (app calls this)."""
    import httpx

    settings = get_settings()
    if not (settings.eleven_agent_id and settings.elevenlabs_api_key):
        raise HTTPException(status_code=503, detail="Realtime voice not configured")
    resp = httpx.get(
        "https://api.elevenlabs.io/v1/convai/conversation/token",
        params={"agent_id": settings.eleven_agent_id},
        headers={"xi-api-key": settings.elevenlabs_api_key},
        timeout=15,
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Could not mint conversation token")
    return {"token": resp.json().get("token"), "agent_id": settings.eleven_agent_id}


def _openai_chunk(rid: str, text: str = "", finish: str | None = None) -> str:
    return "data: " + json.dumps({
        "id": rid, "object": "chat.completion.chunk", "created": int(time.time()),
        "model": "nano-opus",
        "choices": [{"index": 0,
                     "delta": ({"content": text} if text else {}),
                     "finish_reason": finish}],
    }) + "\n\n"


@router.post("/v1/llm/chat/completions")
async def chat_completions(request: Request, db: Session = Depends(get_db),
                           authorization: str = Header(default="")):
    settings = get_settings()
    presented = authorization.removeprefix("Bearer ").strip()
    if not (settings.realtime_secret
            and hmac.compare_digest(presented, settings.realtime_secret)):
        raise HTTPException(status_code=401, detail="Bad realtime secret")

    body = await request.json()
    extra = body.get("elevenlabs_extra_body") or {}
    user_token = str(extra.get("user_token", ""))
    user_id = resolve_token(db, user_token) if user_token else None
    if user_id is None:
        raise HTTPException(status_code=401, detail="Unknown user for this conversation")

    # ElevenLabs sends the whole transcript each turn; we re-ground every time.
    turns = [{"role": "nano" if m["role"] == "assistant" else "user",
              "text": str(m.get("content") or "")[:2000]}
             for m in body.get("messages", []) if m.get("role") in ("user", "assistant")]
    last_user = next((t["text"] for t in reversed(turns) if t["role"] == "user"), "")
    context = get_context(db, agent="hub", user_id=user_id)
    grounding = {
        "conversation": turns[-16:],
        "inbox": _inbox_for_voice(context),
        "nutrition": _nutrition_for_voice(context),
        "scout_tasks": _tasks_for_voice(db, user_id),
        "remembered": recall(db, user_id=user_id, query=last_user or "today", k=4),
        "person": [f for f in context.facts if f["domain"] in ("identity", "inbox", "goals")][:12],
        "knows_person": any(f["domain"] == "identity" for f in context.facts),
    }

    from anthropic import Anthropic

    client = Anthropic(api_key=settings.anthropic_api_key)
    rid = f"chatcmpl-{uuid.uuid4().hex[:12]}"

    def stream():
        spoken_chars = 0
        buffered = ""  # held back once a possible action tag starts
        tag_text = ""
        in_tag = False
        try:
            with client.messages.stream(
                model=settings.model_default,
                max_tokens=400,
                system=[{"type": "text", "text": REALTIME_SYSTEM,
                         "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": json.dumps(grounding, sort_keys=True)}],
            ) as s:
                for delta in s.text_stream:
                    if in_tag:
                        tag_text += delta
                        continue
                    buffered += delta
                    start = buffered.find("<<")
                    if start != -1:
                        speak = buffered[:start]
                        if speak:
                            spoken_chars += len(speak)
                            yield _openai_chunk(rid, speak)
                        tag_text = buffered[start:]
                        buffered = ""
                        in_tag = True
                    elif len(buffered) > 2 and not buffered.endswith("<"):
                        spoken_chars += len(buffered)
                        yield _openai_chunk(rid, buffered)
                        buffered = ""
            if buffered and not in_tag:
                yield _openai_chunk(rid, buffered)
        except Exception:
            yield _openai_chunk(rid, "Sorry — say that again?")
        else:
            _run_action(user_id, tag_text)
        yield _openai_chunk(rid, finish="stop")
        yield "data: [DONE]\n\n"

    def _run_action(uid: str, tag: str) -> None:
        if "<<action:" not in tag:
            return
        try:
            payload = json.loads(tag.split("<<action:", 1)[1].rsplit(">>", 1)[0])
        except (json.JSONDecodeError, IndexError):
            return
        kind = payload.get("type", "")
        # Server-side effects reuse the converse executor verbatim.
        if kind in ("draft_reply", "send_draft", "send_new_email", "set_nutrition",
                    "log_water", "research_task", "connect_site"):
            from ..db import SessionLocal

            adb = SessionLocal()
            try:
                _execute(adb, uid, {
                    "action_type": kind,
                    "draft_id": payload.get("draft_id", ""),
                    "message_id": payload.get("message_id", ""),
                    "reply_body": payload.get("reply_body", ""),
                    "to_addr": payload.get("to_addr", ""),
                    "subject": payload.get("subject", ""),
                    "profile_json": (json.dumps(payload["profile"])
                                     if isinstance(payload.get("profile"), dict)
                                     else payload.get("profile_json", "")),
                })
                append_event(adb, user_id=uid, type="voice_command", agent="orb",
                             payload={"heard": "", "action": kind, "via": "realtime"})
                adb.commit()
            finally:
                adb.close()
        elif kind in ("open_screen", "refresh_inbox", "start_interview"):
            _queue_client_action(uid, {"type": kind, "screen": payload.get("screen", "")})

    return StreamingResponse(stream(), media_type="text/event-stream")
