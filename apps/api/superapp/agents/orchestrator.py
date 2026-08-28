"""The orchestrator — Nano's reflection tier (north star step 4).

Runs nightly (and pre-dawn) via cron. Three duties:
1. REFLECT: one Opus pass over everything that happened across every domain,
   composing the cross-domain brief the Hub and the morning push speak from,
   plus any new cross-domain beliefs worth keeping.
2. REMEMBER: embed the day's mail gists and events into semantic memory so
   the orb can recall them later ("what was that email about the lease?").
3. DECAY: inferred beliefs lose confidence as they age; below 0.3 they are
   archived to events. Identity facts (told to us directly) never decay.
"""
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..llm.provider import LLMProvider
from ..sdui.blocks import Screen
from ..memory import remember
from ..models import InboxMessage, UserFact
from ..push import send_push
from ..substrate import ContextSlice
from .base import EventWrite, FactWrite, ThinkResult, register_agent

REFLECT_SYSTEM = (
    "You are Nano, an AI chief of staff, reflecting at the end of the day on "
    "everything you saw for your person. Write `brief`: what tomorrow's first "
    "glance should say — 2-3 spoken-style sentences, concrete (names, counts, "
    "amounts), leading with what was handled without them, then what waits. "
    "Then `insights`: 0-3 durable cross-domain beliefs this day supports "
    "(patterns, not events — e.g. 'weekday lunches average 900 kcal', "
    "'X emails weekly and expects fast replies'). Only beliefs the evidence "
    "actually supports; empty list is a fine answer."
)
REFLECT_SCHEMA = {
    "type": "object",
    "properties": {
        "brief": {"type": "string"},
        "insights": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "domain": {"type": "string",
                           "enum": ["inbox", "nutrition", "finance", "wardrobe", "goals"]},
                "key": {"type": "string"},
                "belief": {"type": "string"},
                "confidence": {"type": "number"},  # clamped in code; API rejects min/max
            },
            "required": ["domain", "key", "belief", "confidence"],
            "additionalProperties": False,
        }},
    },
    "required": ["brief", "insights"],
    "additionalProperties": False,
}


def _decay(db: Session, user_id: str, result: ThinkResult) -> int:
    """Inferred beliefs age: -10% confidence past 30 days, archived under 0.3.
    Never touches identity (they told us) or system (plumbing) domains."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    stale = db.scalars(select(UserFact).where(
        UserFact.user_id == user_id,
        UserFact.domain.not_in(("identity", "system")),
        UserFact.learned_at < cutoff,
        UserFact.confidence < 1.0,  # 1.0 = user-stated, exempt
    ))
    archived = 0
    for fact in stale:
        fact.confidence = round(fact.confidence * 0.9, 3)
        if fact.confidence < 0.3:
            result.event_writes.append(EventWrite(
                type="fact_decayed", domain=fact.domain,
                payload={"key": fact.key, "value": fact.value,
                         "final_confidence": fact.confidence}))
            db.delete(fact)
            archived += 1
    return archived


def _remember_day(db: Session, context: ContextSlice) -> int:
    """Embed the day's mail into semantic memory (idempotent per message)."""
    day_start = datetime.now(timezone.utc) - timedelta(hours=36)
    msgs = db.scalars(select(InboxMessage).where(
        InboxMessage.user_id == context.user_id,
        InboxMessage.created_at >= day_start))
    n = 0
    for m in msgs:
        gist = m.gist or m.subject
        if not gist:
            continue
        remember(db, user_id=context.user_id, domain="inbox", kind="email",
                 ref_id=m.id, content=f"Email from {m.from_name}: {gist}. "
                                      f"Verdict: {m.tier}. {(m.body_text or '')[:300]}")
        n += 1
    return n


def orchestrator_think(db: Session, *, trigger: dict, context: ContextSlice,
                       run_id: str) -> ThinkResult:
    result = ThinkResult()
    provider = LLMProvider()

    resp = provider.complete(
        db, user_id=context.user_id, agent="orchestrator", task="reflection",
        system=REFLECT_SYSTEM,
        prompt=json.dumps({
            "domain_data": {k: v for k, v in context.domain_data.items()
                            if k not in ("autonomy",)},
            "recent_events": context.recent_events[:40],
            "known_beliefs": context.facts[:40],
        }, sort_keys=True, default=str),
        schema=REFLECT_SCHEMA,
    )
    brief = ""
    if not (resp.stubbed or resp.refused):
        try:
            parsed = json.loads(resp.text)
            brief = parsed["brief"].strip()
            for ins in parsed.get("insights", [])[:3]:
                result.fact_writes.append(FactWrite(
                    domain=ins["domain"], key=f"reflected_{ins['key'][:100]}",
                    value={"belief": ins["belief"][:500]},
                    confidence=min(0.9, max(0.3, float(ins["confidence"])))))
        except (json.JSONDecodeError, KeyError):
            brief = ""
    if not brief:
        inbox = context.domain_data.get("inbox", {})
        asks = len(inbox.get("needs_reply", []))
        brief = (f"Quiet day. {inbox.get('cleared_count', 0)} emails filed, "
                 f"{asks} waiting on you." if inbox.get("connected")
                 else "Quiet day. Nothing needed you.")

    result.fact_writes.append(FactWrite(
        domain="hub", key="reflection_brief",
        value={"text": brief[:900],
               "date": datetime.now(timezone.utc).date().isoformat()},
        confidence=1.0))

    remembered = _remember_day(db, context)
    decayed = _decay(db, context.user_id, result)
    result.event_writes.append(EventWrite(
        type="reflection_run", payload={"remembered": remembered, "decayed": decayed}))

    if trigger.get("kind") == "morning":
        send_push(db, user_id=context.user_id, title="Nano — your morning",
                  body=brief[:170], agent="orchestrator")
    return result


def orchestrator_render(context: ContextSlice) -> Screen:
    return Screen(title="Nano", theme="dark", sections=[])  # no screen; think-only


register_agent("orchestrator", render=orchestrator_render, think=orchestrator_think,
               slow_think=True)
