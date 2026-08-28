"""The orb's voice loop (north-star step 6, pulled forward).

POST /hello  — the orb opened: what should Nano say first?
POST /command — a transcript: parse intent (Haiku routing tier), answer in voice.
GET  /speak  — TTS for any short line Nano says (content-hash cached on disk).
"""
import json

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import current_user_id
from ..db import get_db
from ..llm.provider import LLMProvider
from ..substrate import get_context
from ..substrate.events import append_event
from ..voice import tts

router = APIRouter(prefix="/v1/voice", tags=["voice"])

INTENT_SYSTEM = (
    "You are Nano's ear: route what the person said to an app intent. Screens: "
    "hub (home/overview), inbox (email/mail/messages), home (nutrition/meals/"
    "food), finance (money/spending/bank), stylist (clothes/outfits/wardrobe). "
    "Intents: open_screen (navigate), refresh_inbox (check/sync mail), "
    "start_interview (they agree to or ask for the get-to-know-you talk), "
    "answer (a question you can answer from context — put the answer in say), "
    "none (unclear). `say` is Nano's spoken reply: one short, warm, human "
    "sentence — never scripted-sounding, never more than ~20 words."
)
INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string",
                   "enum": ["open_screen", "refresh_inbox", "start_interview", "answer", "none"]},
        "screen": {"type": "string", "enum": ["hub", "inbox", "home", "finance", "stylist", ""]},
        "say": {"type": "string"},
    },
    "required": ["intent", "screen", "say"],
    "additionalProperties": False,
}


class CommandBody(BaseModel):
    transcript: str = Field(min_length=1, max_length=2000)


def _stub_intent(text: str) -> dict:
    t = text.lower()
    for screen, words in [("inbox", ("mail", "email", "inbox")), ("home", ("meal", "food", "eat")),
                          ("finance", ("money", "spend", "bank")), ("stylist", ("wear", "outfit", "cloth")),
                          ("hub", ("hub", "home", "overview"))]:
        if any(w in t for w in words):
            return {"intent": "open_screen", "screen": screen, "say": f"Opening your {screen}."}
    if any(w in t for w in ("yes", "sure", "know me", "interview", "let's")):
        return {"intent": "start_interview", "screen": "", "say": "Wonderful — let's talk."}
    return {"intent": "none", "screen": "", "say": "I didn't catch that — try 'show my inbox'."}


@router.post("/hello")
def hello(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    """The orb just opened. If Nano doesn't know this person yet, it asks."""
    context = get_context(db, agent="hub", user_id=user_id)
    has_identity = any(f["domain"] == "identity" for f in context.facts)
    name = context.user_name
    if not has_identity:
        say = (f"Hi {name} — I'm Nano. Before I run the boring half of your life, "
               "I'd love to actually get to know you. About thirty minutes, and we "
               "can stop any time. Want to start?")
        offer = "interview"
    else:
        say = "I'm listening."
        offer = None
    return {"say": say, "offer": offer}


@router.post("/command")
def command(body: CommandBody, user_id: str = Depends(current_user_id),
            db: Session = Depends(get_db)):
    context = get_context(db, agent="hub", user_id=user_id)
    provider = LLMProvider()
    resp = provider.complete(
        db, user_id=user_id, agent="orb", task="voice_intent",
        system=INTENT_SYSTEM,
        prompt=json.dumps({
            "heard": body.transcript,
            "context_hint": {
                "asks_waiting": len(context.domain_data.get("inbox", {}).get("needs_reply", [])),
                "knows_person": any(f["domain"] == "identity" for f in context.facts),
            },
        }, sort_keys=True),
        schema=INTENT_SCHEMA,
    )
    if resp.stubbed or resp.refused:
        parsed = _stub_intent(body.transcript)
    else:
        try:
            parsed = json.loads(resp.text)
        except json.JSONDecodeError:
            parsed = _stub_intent(body.transcript)
    append_event(db, user_id=user_id, type="voice_command", agent="orb",
                 payload={"heard": body.transcript[:200], "intent": parsed["intent"],
                          "screen": parsed.get("screen", "")})
    db.commit()
    return parsed


@router.get("/speak")
def speak(text: str, user_id: str = Depends(current_user_id)):
    audio = tts(text[:600])
    if not audio:
        return Response(status_code=204)
    return Response(content=audio, media_type="audio/mpeg")
