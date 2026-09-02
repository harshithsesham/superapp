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
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import current_user_id
from ..config import get_settings
from ..db import get_db
from ..inbox.gmail_client import GmailClient
from ..kernel import record_decision
from ..llm.provider import LLMProvider
from ..memory import recall, remember
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
    "Writing a NEW email (not a reply): they must give you the address — "
    "NEVER guess or invent one. Compose it in their voice, read it back in "
    "`say` (action=none, listen=true). Only after they explicitly confirm "
    "sending THAT draft: action=send_new_email with to_addr, subject, "
    "reply_body. New recipients always get this read-back-and-confirm, "
    "no exceptions.\n"
    "CONNECTING ACCOUNTS: when they ask to connect/log into a site for the "
    "scout (e.g. 'connect facebook'): action=connect_site, reply_body=site "
    "name. Tell them a login window will be ready in under a minute at their "
    "scout link, and it stays open twenty minutes.\n"
    "FLIGHT WATCHES (the Flycatcher): when they ask to WATCH or TRACK flight "
    "prices over time ('watch flights to Hyderabad in December', 'tell me when "
    "it drops under $900'), emit action=research_task with reply_body starting "
    "with 'watch flights' plus route, dates, and any target price. Tell them "
    "you'll check daily and only ping them on a real drop. A one-time 'find me "
    "flights' (no watching) is a normal errand, not a watch. When they want to "
    "STOP anything the scout does ('stop the watch', 'cancel flight tracking', "
    "'stop all scout jobs'): action=research_task with reply_body='stop all "
    "scout errands and watches'.\n"
    "RESEARCH ERRANDS: when they ask you to find/search/scout something out "
    "in the world (homes, used goods, prices, options — anything needing the "
    "web), emit action=research_task with reply_body = a crisp self-contained "
    "instruction (what, where, budget, constraints). Tell them you're on it "
    "and will ping them when the shortlist is ready. scout_tasks in context "
    "holds recent errands and their results — answer 'what did you find' "
    "from there, reading the shortlist naturally.\n"
    "nutrition in context is their live day — plan targets, what they ate, "
    "kcal_left, water — answer calorie/macro/water questions from it with "
    "real numbers, never estimates of your own.\n"
    "recently_sent in context is the record of what YOU sent for them — "
    "when they ask what was sent, answer from it concretely (who, what, when).\n"
    "They log water by voice ('log a glass of water', 'I drank a bottle'): "
    "action=log_water, reply_body carries the millilitres as digits (glass "
    "~250, bottle ~500).\n"
    "NUTRITION SETUP: when they want a meal/calorie plan (or ask to set up "
    "nutrition), collect conversationally — sex, birth year, height, current "
    "weight, TARGET weight (what they want to reach), and workouts per week "
    "(map to activity: limited/moderate/athlete). One or two questions at a "
    "time, accept any units (convert to kg/cm). When you have it all, emit "
    "action=set_nutrition with profile_json (keys: sex, born_year, height_cm, "
    "weight_kg, target_weight_kg, activity) — the goal is derived from where "
    "their weight is versus where they want it. Tell them the plan is ready "
    "and what it is. Partial updates are fine ('I'm 74 kilos now').\n"
    "When they wrap up (goodbye, that's all, thanks I'm done): "
    "action=end_conversation with a short warm sign-off in say.\n"
    "Navigation requests: action=open_screen with screen "
    "(hub|inbox|home|finance|stylist|flights). Mail check: action=refresh_inbox. "
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
                                 "draft_reply", "send_draft", "send_new_email",
                                 "set_nutrition", "log_water", "research_task",
                                 "connect_site", "end_conversation"]},
        "screen": {"type": "string", "enum": ["hub", "inbox", "home", "finance", "stylist", "flights", ""]},
        "draft_id": {"type": "string"},
        "message_id": {"type": "string"},
        "reply_body": {"type": "string"},
        "to_addr": {"type": "string"},
        "subject": {"type": "string"},
        "profile_json": {"type": "string"},
        "listen": {"type": "boolean"},
    },
    "required": ["say", "action_type", "screen", "draft_id", "message_id", "reply_body",
                 "to_addr", "subject", "profile_json", "listen"],
    "additionalProperties": False,
}


class Turn(BaseModel):
    role: str = Field(pattern="^(user|nano)$")
    text: str = Field(max_length=4000)


class ConverseBody(BaseModel):
    messages: list[Turn] = Field(min_length=1, max_length=40)


def _tasks_for_voice(db, user_id: str) -> list[dict]:
    from sqlalchemy import select

    from ..models import AgentTask

    rows = db.scalars(select(AgentTask).where(AgentTask.user_id == user_id)
                      .order_by(AgentTask.created_at.desc()).limit(3))
    return [{"instruction": t.instruction[:150], "status": t.status,
             "result": t.result if t.status == "done" else None,
             "error": t.error} for t in rows]


def _nutrition_for_voice(context) -> dict:
    """What Nano needs to answer 'how many calories are left' and kin."""
    data = context.domain_data.get("nutrition", {})
    plan = next((f["value"] for f in context.facts
                 if f["domain"] == "nutrition" and f["key"] == "plan"), None)
    today = data.get("today", {})
    out = {
        "plan": plan,
        "today": {k: today.get(k) for k in ("kcal", "protein_g", "carbs_g", "fat_g",
                                            "fiber_g", "sugar_g", "sodium_mg", "water_ml")},
        "meals_today": [{"what": m.get("description"), "kcal": m.get("kcal")}
                        for m in today.get("meals", [])][:8],
        "activity": data.get("activity"),
    }
    if plan and plan.get("kcal") is not None:
        out["kcal_left"] = max(plan["kcal"] - (today.get("kcal") or 0), 0)
        out["water_ml_left"] = max(plan.get("water_ml", 0) - (today.get("water_ml") or 0), 0)
    return out


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
        "recently_sent": [{
            "to": x["to_name"] or x["to_addr"], "subject": x["subject"],
            "body_excerpt": x["body"][:300], "sent_at": x["sent_at"],
        } for x in inbox.get("sent", [])[:6]],
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
                          ("flights", ("flight", "flights")),
                          ("finance", ("money", "spend")), ("stylist", ("wear", "outfit")),
                          ("hub", ("hub", "overview"))]:
        if any(w in t for w in words):
            return {**base, "action_type": "open_screen", "screen": screen,
                    "say": f"Opening your {screen}."}
    if any(w in t for w in ("yes", "sure", "let's", "start")):
        return {**base, "action_type": "start_interview", "say": "Wonderful — let's talk."}
    return {**base, "say": "Say that once more?", "listen": True}


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
    if action == "set_nutrition" and parsed.get("profile_json"):
        from ..nutrition_plan import save_profile_and_plan

        try:
            incoming = json.loads(parsed["profile_json"])
        except json.JSONDecodeError:
            return {"say": "I didn't quite get those numbers — run them by me again?"}
        plan = save_profile_and_plan(db, user_id, incoming)
        if plan is None:
            return {"say": "I didn't quite get those numbers — run them by me again?"}
        append_event(db, user_id=user_id, type="nutrition_plan_set", agent="orb",
                     domain="nutrition", payload={k: plan[k] for k in ("kcal", "goal")})
        return {}
    if action == "connect_site" and parsed.get("reply_body"):
        from ..models import AgentTask
        site = parsed["reply_body"].strip().lower()[:40]
        task = AgentTask(user_id=user_id, kind="connect_login",
                         instruction=f"connect {site}")
        db.add(task)
        db.flush()
        append_event(db, user_id=user_id, type="task_queued", agent="orb",
                     payload={"task_id": task.id, "kind": "connect_login", "site": site})
        return {}
    if action == "research_task" and parsed.get("reply_body"):
        import re as _re

        from sqlalchemy import select as _select

        from ..models import AgentTask, FlightWatch
        instruction = parsed["reply_body"][:1000].strip()
        if (_re.search(r"\b(stop|cancel|remove|delete|end|kill)\b", instruction, _re.I)
                and _re.search(r"\b(watch(?:ing|es)?|track(?:ing)?|scout(?:ing)?"
                               r"|errands?|tasks?|jobs?)\b", instruction, _re.I)):
            stopped = 0
            for w in db.scalars(_select(FlightWatch).where(
                    FlightWatch.user_id == user_id, FlightWatch.active.is_(True))):
                w.active = False
                w.updated_at = utcnow()
                stopped += 1
            cancelled = 0
            for t in db.scalars(_select(AgentTask).where(
                    AgentTask.user_id == user_id, AgentTask.status == "queued")):
                t.status = "failed"
                t.error = "Cancelled by you."
                t.updated_at = utcnow()
                cancelled += 1
            append_event(db, user_id=user_id, type="task_queued", agent="orb",
                         payload={"kind": "scout_stopped", "watches": stopped,
                                  "cancelled": cancelled})
            if stopped == 0 and cancelled == 0:
                return {"say": "The scout is already idle — no watches and "
                               "nothing queued."}
            parts = []
            if stopped:
                parts.append(f"stopped {stopped} flight "
                             f"{'watch' if stopped == 1 else 'watches'}")
            if cancelled:
                parts.append(f"cancelled {cancelled} queued "
                             f"{'errand' if cancelled == 1 else 'errands'}")
            return {"say": f"Done — {' and '.join(parts)}. Anything already "
                           "mid-run finishes within a minute and won't repeat."}
        if (_re.search(r"\b(watch|track|alert|monitor)\b", instruction, _re.I)
                and _re.search(r"\bflights?\b", instruction, _re.I)):
            m = _re.search(r"(?:under|below|target)\s*\$?\s*(\d[\d,]*)",
                           instruction, _re.I)
            target = int(m.group(1).replace(",", "")) if m else None
            watch = FlightWatch(user_id=user_id, instruction=instruction[:500],
                                target_price=target)
            db.add(watch)
            db.flush()
            db.add(AgentTask(user_id=user_id, kind="flights",
                             instruction=watch.instruction, watch_id=watch.id))
            append_event(db, user_id=user_id, type="task_queued", agent="orb",
                         payload={"watch_id": watch.id, "kind": "flight_watch",
                                  "instruction": watch.instruction[:200]})
            return {}
        kind = "flights" if _re.search(r"\bflights?\b", instruction, _re.I) else (
            "marketplace" if "marketplace" in instruction.lower() else "research")
        task = AgentTask(user_id=user_id, kind=kind, instruction=instruction)
        db.add(task)
        db.flush()
        append_event(db, user_id=user_id, type="task_queued", agent="orb",
                     payload={"task_id": task.id, "instruction": task.instruction[:200],
                              "via": "voice"})
        return {}
    if action == "log_water":
        try:
            ml = max(50, min(2000, int(float(parsed.get("reply_body") or 250))))
        except ValueError:
            ml = 250
        append_event(db, user_id=user_id, type="water_logged", agent="orb",
                     domain="nutrition", payload={"ml": ml, "via": "voice"})
        return {}
    if action == "send_new_email" and parsed.get("to_addr") and parsed.get("reply_body"):
        settings = get_settings()
        if settings.gmail_scope_tier not in ("send", "modify"):
            return {"say": "Sending is still switched off — I can draft, but you send."}
        addr = parsed["to_addr"].strip()
        if "@" not in addr or " " in addr:
            return {"say": "I don't have a real address for them — spell it out for me?"}
        from ..substrate.inbox import accounts
        accts = accounts(db, user_id)
        if not accts:
            return {"say": "No mailbox is connected yet."}
        token = get_token(db, user_id=user_id, provider=f"gmail:{accts[0].email}")
        client = GmailClient(json.loads(token) if token else None)
        subject = parsed.get("subject") or "(no subject)"
        sent_id = client.send_new(to_addr=addr, subject=subject, body=parsed["reply_body"])
        append_event(db, user_id=user_id, type="email_sent_new", agent="orb", domain="inbox",
                     payload={"to": addr, "subject": subject,
                              "body": parsed["reply_body"][:2000],
                              "gmail_sent_id": sent_id, "via": "voice"})
        remember(db, user_id=user_id, domain="inbox", kind="sent", ref_id=sent_id,
                 content=f"Nano sent an email to {addr} — {subject}: "
                         f"{parsed['reply_body'][:600]}")
        # New recipient: hard-capped at ask-first in the kernel, forever.
        record_decision(db, user_id=user_id, agent="inbox",
                        action_key="inbox.send_new_recipient", decided_by="user",
                        verdict="accepted", payload={"to": addr, "via": "voice"})
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
        remember(db, user_id=user_id, domain="inbox", kind="sent", ref_id=draft.id,
                 content=f"Nano replied to {msg.from_name} ({msg.from_addr}) — "
                         f"{msg.subject}: {draft.body[:600]}")
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
            "nutrition": _nutrition_for_voice(context),
            "scout_tasks": _tasks_for_voice(db, user_id),
            "remembered": recall(db, user_id=user_id,
                                 query=body.messages[-1].text, k=4),
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
                          "said": parsed["say"][:200],
                          "action": parsed["action_type"], "screen": parsed.get("screen", "")})
    if parsed["action_type"] == "end_conversation" and len(body.messages) > 1:
        convo = " / ".join(f"{t.role}: {t.text[:150]}" for t in body.messages[-12:])
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
        remember(db, user_id=user_id, domain="identity", kind="conversation",
                 ref_id=f"voice-{stamp}",
                 content=f"Voice conversation with Nano: {convo[:1600]}")
    db.commit()
    return {
        "say": parsed["say"], "action": parsed["action_type"],
        "screen": parsed.get("screen", ""), "listen": parsed.get("listen", False),
        "acted": parsed["action_type"] in ("draft_reply", "send_draft", "send_new_email",
                                           "set_nutrition", "log_water"),
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
