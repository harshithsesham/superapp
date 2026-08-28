"""The orb's voice loop — a conversational agent, not a command router.

POST /hello    — the orb opened: what should Nano say first?
POST /converse — multi-turn conversation with full context and real actions:
                 summarize what needs attention, rewrite a waiting reply,
                 send it on the person's explicit spoken yes.
GET  /speak    — TTS for anything Nano says (content-hash cached on disk).

The spoken "send it" carries exactly the trust of the send button: same
authenticated user, same trust-ladder gate, same decision-ledger row.
"""
import json

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import current_user_id
from ..config import get_settings
from ..db import get_db
from ..inbox.gmail_client import GmailClient
from ..kernel import record_decision
from ..llm.provider import LLMProvider
from ..models import InboxMessage, utcnow
from ..substrate import get_context
from ..substrate.events import append_event
from ..substrate.inbox import create_draft, get_draft
from ..vault import get_token
from ..voice import tts

router = APIRouter(prefix="/v1/voice", tags=["voice"])

CONVERSE_SYSTEM = (
    "You are Nano, a voice-first chief of staff, SPEAKING OUT LOUD to your "
    "person. Everything in `say` is spoken audio: natural, warm, specific, "
    "under ~60 words, never bullet points, never markdown, never scripted "
    "filler. You have their real inbox in context.\n"
    "When they ask what needs attention: tell them concretely — who wrote, "
    "what they want, that your reply is already drafted — then offer the next "
    "step (read it, change it, send it).\n"
    "When they ask you to read a draft or an email: read the substance aloud, "
    "compressed, not verbatim boilerplate.\n"
    "When they ask you to change or write a reply: action=draft_reply with "
    "message_id and the full new body, written in their voice (use "
    "reply_style/identity facts; sign with their name; NEVER invent facts, "
    "names, or commitments they didn't state).\n"
    "Send ONLY when they explicitly say to send THIS message: "
    "action=send_draft with draft_id. Never send unasked. If they decline, "
    "move on gracefully.\n"
    "Navigation requests: action=open_screen with screen "
    "(hub|inbox|home|finance|stylist). Mail check: action=refresh_inbox. "
    "They agree to the get-to-know-you conversation: action=start_interview.\n"
    "Set listen=true whenever you ask a question or the conversation is "
    "mid-task; listen=false when your reply naturally ends the exchange."
)

CONVERSE_SCHEMA = {
    "type": "object",
    "properties": {
        "say": {"type": "string"},
        "action_type": {"type": "string",
                        "enum": ["none", "open_screen", "refresh_inbox", "start_interview",
                                 "draft_reply", "send_draft"]},
        "screen": {"type": "string", "enum": ["hub", "inbox", "home", "finance", "stylist", ""]},
        "draft_id": {"type": "string"},
        "message_id": {"type": "string"},
        "reply_body": {"type": "string"},
        "listen": {"type": "boolean"},
    },
    "required": ["say", "action_type", "screen", "draft_id", "message_id", "reply_body", "listen"],
    "additionalProperties": False,
}


class Turn(BaseModel):
    role: str = Field(pattern="^(user|nano)$")
    text: str = Field(max_length=4000)


class ConverseBody(BaseModel):
    messages: list[Turn] = Field(min_length=1, max_length=40)


def _inbox_for_voice(context) -> dict:
    """The slice Nano talks from: compact, but real content — not counts."""
    inbox = context.domain_data.get("inbox", {})
    return {
        "needs_reply": [{
            "message_id": a["id"], "from": a["from_name"], "subject": a["subject"],
            "what_they_want": a["why_now"] or a["gist"],
            "body_excerpt": (a.get("body") or "")[:400],
            "draft_id": (a.get("draft") or {}).get("id"),
            "draft_body": (a.get("draft") or {}).get("body", "")[:400],
            "deferred": (a.get("draft") or {}).get("deferred", False),
        } for a in inbox.get("needs_reply", [])[:6]],
        "worth_knowing": [{
            "from": r["from_name"], "gist": r["gist"] or r["subject"],
        } for r in inbox.get("worth_knowing", [])[:6]],
        "cleared_count": inbox.get("cleared_count", 0),
        "sending_enabled": get_settings().gmail_scope_tier in ("send", "modify"),
    }


def _stub_converse(user_text: str, voice_inbox: dict) -> dict:
    t = user_text.lower()
    base = {"say": "", "action_type": "none", "screen": "", "draft_id": "",
            "message_id": "", "reply_body": "", "listen": False}
    asks = voice_inbox["needs_reply"]
    if any(w in t for w in ("attention", "need", "important", "read")):
        if asks:
            names = ", ".join(a["from"] for a in asks[:3])
            base["say"] = (f"{len(asks)} email{'s' if len(asks) != 1 else ''} need you — from {names}. "
                           f"First: {asks[0]['from']} — {asks[0]['what_they_want']}. "
                           "My reply is drafted. Want me to read it or send it?")
            base["listen"] = True
        else:
            base["say"] = "Nothing needs your words right now. Your inbox is clear."
        return base
    if "send" in t and asks and asks[0]["draft_id"]:
        return {**base, "action_type": "send_draft", "draft_id": asks[0]["draft_id"],
                "say": "Sent."}
    for screen, words in [("inbox", ("mail", "email", "inbox")), ("home", ("meal", "food")),
                          ("finance", ("money", "spend")), ("stylist", ("wear", "outfit")),
                          ("hub", ("hub", "overview"))]:
        if any(w in t for w in words):
            return {**base, "action_type": "open_screen", "screen": screen,
                    "say": f"Opening your {screen}."}
    if any(w in t for w in ("yes", "sure", "let's", "start")):
        return {**base, "action_type": "start_interview", "say": "Wonderful — let's talk."}
    return {**base, "say": "Tell me what you need — your inbox, or anything on your plate.",
            "listen": True}


def _execute(db: Session, user_id: str, parsed: dict) -> dict:
    """Run the model's action server-side. Returns adjustments to speak."""
    action = parsed["action_type"]
    if action == "draft_reply" and parsed["message_id"] and parsed["reply_body"]:
        msg = db.get(InboxMessage, parsed["message_id"])
        if msg is None or msg.user_id != user_id:
            return {"say": "I lost track of that email — try again from the inbox."}
        if parsed["draft_id"]:
            try:
                draft = get_draft(db, user_id=user_id, draft_id=parsed["draft_id"])
                append_event(db, user_id=user_id, type="draft_edited", agent="orb", domain="inbox",
                             payload={"draft_id": draft.id, "before": draft.body[:2000],
                                      "after": parsed["reply_body"][:2000], "via": "voice"})
                draft.body = parsed["reply_body"]
                draft.status = "edited"
            except ValueError:
                return {"say": "I couldn't find that draft."}
        else:
            create_draft(db, user_id=user_id, message_id=msg.id, body=parsed["reply_body"])
            append_event(db, user_id=user_id, type="draft_created", agent="orb", domain="inbox",
                         payload={"message_id": msg.id, "via": "voice"})
        return {}
    if action == "send_draft" and parsed["draft_id"]:
        settings = get_settings()
        if settings.gmail_scope_tier not in ("send", "modify"):
            return {"say": "Sending is still switched off — I can draft, but you send."}
        try:
            draft = get_draft(db, user_id=user_id, draft_id=parsed["draft_id"])
        except ValueError:
            return {"say": "I couldn't find that draft."}
        if draft.status == "sent":
            return {"say": "That one already went out."}
        was_edited = draft.status == "edited"
        msg = db.get(InboxMessage, draft.message_id)
        token = get_token(db, user_id=user_id, provider=f"gmail:{msg.account_email}")
        client = GmailClient(json.loads(token) if token else None)
        sent_id = client.send_reply(to_addr=msg.from_addr, subject=msg.subject,
                                    body=draft.body, thread_id=msg.thread_id)
        draft.status = "sent"
        draft.sent_at = utcnow()
        msg.settled = True
        append_event(db, user_id=user_id, type="draft_sent", agent="orb", domain="inbox",
                     payload={"draft_id": draft.id, "gmail_sent_id": sent_id,
                              "edited": was_edited, "via": "voice"})
        # The spoken yes is a typed verdict, same as the tap.
        record_decision(db, user_id=user_id, agent="inbox", action_key="inbox.send_reply",
                        decided_by="user", verdict="edited" if was_edited else "accepted",
                        payload={"draft_id": draft.id, "via": "voice"})
        return {}
    return {}


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


@router.post("/converse")
def converse(body: ConverseBody, user_id: str = Depends(current_user_id),
             db: Session = Depends(get_db)):
    context = get_context(db, agent="hub", user_id=user_id)
    voice_inbox = _inbox_for_voice(context)
    provider = LLMProvider()
    resp = provider.complete(
        db, user_id=user_id, agent="orb", task="voice_converse",
        system=CONVERSE_SYSTEM,
        prompt=json.dumps({
            "conversation": [t.model_dump() for t in body.messages[-16:]],
            "inbox": voice_inbox,
            "person": [f for f in context.facts if f["domain"] in ("identity", "inbox", "goals")][:12],
            "knows_person": any(f["domain"] == "identity" for f in context.facts),
        }, sort_keys=True),
        schema=CONVERSE_SCHEMA,
    )
    if resp.stubbed or resp.refused:
        parsed = _stub_converse(body.messages[-1].text, voice_inbox)
    else:
        try:
            parsed = json.loads(resp.text)
        except json.JSONDecodeError:
            parsed = _stub_converse(body.messages[-1].text, voice_inbox)

    override = _execute(db, user_id, parsed)
    if override.get("say"):
        parsed["say"] = override["say"]
        parsed["listen"] = True

    append_event(db, user_id=user_id, type="voice_command", agent="orb",
                 payload={"heard": body.messages[-1].text[:200],
                          "action": parsed["action_type"], "screen": parsed.get("screen", "")})
    db.commit()
    return {
        "say": parsed["say"], "action": parsed["action_type"],
        "screen": parsed.get("screen", ""), "listen": parsed.get("listen", False),
        "acted": parsed["action_type"] in ("draft_reply", "send_draft"),
    }


class CommandBody(BaseModel):
    transcript: str = Field(min_length=1, max_length=2000)


@router.post("/command")
def command(body: CommandBody, user_id: str = Depends(current_user_id),
            db: Session = Depends(get_db)):
    """Back-compat for app builds that speak the old one-shot shape."""
    result = converse(ConverseBody(messages=[Turn(role="user", text=body.transcript)]),
                      user_id=user_id, db=db)
    intent = {"open_screen": "open_screen", "refresh_inbox": "refresh_inbox",
              "start_interview": "start_interview"}.get(result["action"], "answer")
    return {"intent": intent, "screen": result["screen"], "say": result["say"]}


@router.get("/speak")
def speak(text: str, user_id: str = Depends(current_user_id)):
    audio = tts(text[:600])
    if not audio:
        return Response(status_code=204)
    return Response(content=audio, media_type="audio/mpeg")
