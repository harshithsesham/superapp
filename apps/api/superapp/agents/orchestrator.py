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

PLAYBOOK_SYSTEM = (
    "You distill PROCEDURES, not beliefs: from this ledger of decisions and "
    "events, find recurring situations the assistant handled the same way "
    "more than once, and write each as a playbook — when it applies and "
    "exactly how to handle it, including the user's observed preferences "
    "(tone, timing, who gets what). Only procedures the evidence shows at "
    "least twice; 0-2 per night; empty list is the normal answer. slug: "
    "kebab-case, max 40 chars. when/how: one plain sentence each."
)
PLAYBOOK_SCHEMA = {
    "type": "object",
    "properties": {"playbooks": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "slug": {"type": "string"},
            "when": {"type": "string"},
            "how": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["slug", "when", "how", "confidence"],
        "additionalProperties": False,
    }}},
    "required": ["playbooks"],
    "additionalProperties": False,
}

CONSOLIDATE_SYSTEM = (
    "You are tidying a belief store during sleep. Given reflected beliefs, "
    "find groups that say the same thing (or where one supersedes another) "
    "and merge each group: keep one key, fold the wording into one crisp "
    "belief, list the keys to drop. Merge ONLY true near-duplicates; when in "
    "doubt, leave beliefs alone. Empty list is the normal answer."
)
CONSOLIDATE_SCHEMA = {
    "type": "object",
    "properties": {"merges": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "domain": {"type": "string"},
            "keep_key": {"type": "string"},
            "merged_belief": {"type": "string"},
            "drop_keys": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["domain", "keep_key", "merged_belief", "drop_keys"],
        "additionalProperties": False,
    }}},
    "required": ["merges"],
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


WAKE_SYSTEM = (
    "You are the wake-decision layer of a personal chief of staff. You see a "
    "digest of pending signals and when the person was last nudged about "
    "each. The DEFAULT IS SILENCE: wake only if a thoughtful human assistant "
    "would tap the person's shoulder right now — a reply aging toward a real "
    "deadline, access broken for hours, something they said matters going "
    "stale. Routine state (a few unread notes, watches ticking along) is "
    "never worth a wake. If waking, compose ONE calm message under 140 "
    "characters saying the single most important thing and what to do."
)
WAKE_SCHEMA = {
    "type": "object",
    "properties": {
        "wake": {"type": "boolean"},
        "reason": {"type": "string"},
        "message": {"type": "string"},
        "topics": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["wake", "reason", "message", "topics"],
    "additionalProperties": False,
}


def _heartbeat_signals(db: Session, context: ContextSlice) -> list[dict]:
    """Cheap, LLM-free signal gathering. Each signal has a stable topic key
    so the dedup memory can prevent re-nagging."""
    from datetime import timedelta

    from ..models import AgentTask, InboxDraft, InboxMessage, UserFact

    now = datetime.now(timezone.utc)
    signals: list[dict] = []

    inbox = context.domain_data.get("inbox", {})
    for a in inbox.get("needs_reply", []):
        d = a.get("draft") or {}
        if d.get("status") == "sent" or d.get("deferred"):
            continue
        try:
            age_h = (now - datetime.fromisoformat(a["received_at"])).total_seconds() / 3600
        except (KeyError, ValueError):
            age_h = 0
        signals.append({"topic": f"ask:{a['id']}", "kind": "reply_waiting",
                        "from": a.get("from_name", ""), "subject": a.get("subject", ""),
                        "urgency": a.get("why_now", ""), "age_hours": round(age_h, 1)})

    overdue = db.scalar(select(InboxDraft).where(
        InboxDraft.user_id == context.user_id, InboxDraft.status.in_(("waiting", "edited")),
        InboxDraft.defer_until.isnot(None), InboxDraft.defer_until < now).limit(1))
    if overdue is not None:
        signals.append({"topic": f"deferred:{overdue.id}", "kind": "deferred_due",
                        "note": "a held draft passed its 6pm"})

    flag = db.scalar(select(UserFact).where(
        UserFact.user_id == context.user_id, UserFact.domain == "inbox",
        UserFact.key == "reauth_needed"))
    if flag and (flag.value or {}).get("needed"):
        signals.append({"topic": "reauth", "kind": "gmail_disconnected",
                        "since": (flag.value or {}).get("since", "")})

    cutoff = now - timedelta(hours=6)
    failed = list(db.scalars(select(AgentTask).where(
        AgentTask.user_id == context.user_id, AgentTask.status == "failed",
        AgentTask.updated_at > cutoff).limit(3)))
    for t in failed:
        signals.append({"topic": f"taskfail:{t.id}", "kind": "errand_failed",
                        "errand": t.instruction[:80]})
    return signals


def _heartbeat(db: Session, context: ContextSlice, result: ThinkResult) -> ThinkResult:
    """The silent heartbeat: gather signals cheaply, let Haiku decide if any
    are worth a human's attention, speak at most once — else stay silent."""
    from ..models import UserFact

    provider = LLMProvider()
    now = datetime.now(timezone.utc)

    state_fact = db.scalar(select(UserFact).where(
        UserFact.user_id == context.user_id, UserFact.domain == "orchestrator",
        UserFact.key == "heartbeat_state"))
    notified: dict = (state_fact.value or {}).get("notified", {}) if state_fact else {}

    def fresh(topic: str, hours: float = 8.0) -> bool:
        ts = notified.get(topic)
        if not ts:
            return True
        try:
            return (now - datetime.fromisoformat(ts)).total_seconds() > hours * 3600
        except ValueError:
            return True

    signals = [s for s in _heartbeat_signals(db, context)
               if fresh(s["topic"], 24.0 if s["topic"] == "reauth" else 8.0)]
    if not signals:
        result.event_writes.append(EventWrite(
            type="heartbeat", payload={"wake": False, "signals": 0}))
        return result

    # Cheap wake decision (task=classification routes to Haiku at low effort).
    resp = provider.complete(
        db, user_id=context.user_id, agent="orchestrator", task="classification",
        system=WAKE_SYSTEM,
        prompt=json.dumps({
            "local_utc": now.isoformat(timespec="minutes"),
            "signals": signals[:8],
        }, sort_keys=True),
        schema=WAKE_SCHEMA,
    )
    wake, message, topics = False, "", []
    if not (resp.stubbed or resp.refused):
        try:
            parsed = json.loads(resp.text)
            wake = bool(parsed.get("wake"))
            message = str(parsed.get("message", ""))[:170]
            topics = [str(t)[:80] for t in parsed.get("topics", [])][:8]
        except json.JSONDecodeError:
            wake = False

    if wake and message:
        send_push(db, user_id=context.user_id, title="Nano",
                  body=message, agent="orchestrator")
        try:
            from ..config import get_settings as _gs
            from ..routers.telegram import _chat_map, _send as _tg_send
            for chat_id, uid in _chat_map().items():
                if uid == context.user_id:
                    _tg_send(chat_id, message)
        except Exception:  # noqa: BLE001
            pass
        for t in (topics or [s["topic"] for s in signals]):
            notified[t] = now.isoformat()
        result.fact_writes.append(FactWrite(
            domain="orchestrator", key="heartbeat_state",
            value={"notified": dict(list(notified.items())[-40:])}, confidence=1.0))
    result.event_writes.append(EventWrite(
        type="heartbeat", payload={"wake": wake, "signals": len(signals),
                                   "message": message if wake else ""}))
    return result


def _dream(db: Session, context: ContextSlice, result: ThinkResult) -> dict:
    """Sleep-time consolidation (Nano 2.0 Phase C): merge near-duplicate
    reflected beliefs, distill procedural playbooks from the decision
    ledger, and prune long-settled mail rows (their gists already live in
    semantic memory). Cheap models, deterministic writes, one dream event."""
    from ..models import Decision, InboxMessage, UserFact
    from ..substrate.facts import write_fact

    provider = LLMProvider()
    report = {"consolidated": 0, "dropped_facts": 0, "playbooks": 0,
              "pruned_messages": 0}

    # 1. Consolidate reflected_* beliefs when they've piled up.
    reflected = [f for f in db.scalars(select(UserFact).where(
        UserFact.user_id == context.user_id,
        UserFact.key.like("reflected_%")))]
    if len(reflected) >= 6:
        resp = provider.complete(
            db, user_id=context.user_id, agent="orchestrator",
            task="classification", system=CONSOLIDATE_SYSTEM,
            prompt=json.dumps({"beliefs": [
                {"domain": f.domain, "key": f.key,
                 "belief": (f.value or {}).get("belief", ""),
                 "confidence": f.confidence} for f in reflected[:30]],
            }, sort_keys=True),
            schema=CONSOLIDATE_SCHEMA,
        )
        if not (resp.stubbed or resp.refused):
            try:
                merges = json.loads(resp.text).get("merges", [])[:6]
            except json.JSONDecodeError:
                merges = []
            by_key = {(f.domain, f.key): f for f in reflected}
            fresh_keys = {(w.domain, w.key) for w in result.fact_writes}
            for m in merges:
                touched = [(m["domain"], m["keep_key"])] + [
                    (m["domain"], k) for k in m.get("drop_keys", [])]
                if any(t in fresh_keys for t in touched):
                    continue  # tonight's reflection re-emitted it; leave alone
                keep = by_key.get((m["domain"], m["keep_key"]))
                drops = [by_key[(m["domain"], k)] for k in m.get("drop_keys", [])
                         if (m["domain"], k) in by_key and k != m["keep_key"]]
                if keep is None or not drops:
                    continue
                keep.value = {"belief": str(m["merged_belief"])[:500]}
                keep.learned_at = datetime.now(timezone.utc)
                for d in drops:
                    result.event_writes.append(EventWrite(
                        type="fact_consolidated", domain=d.domain,
                        payload={"dropped_key": d.key, "into": keep.key,
                                 "old_value": d.value}))
                    db.delete(d)
                    report["dropped_facts"] += 1
                report["consolidated"] += 1

    # 2. Distill playbooks from how decisions actually went.
    decisions = list(db.scalars(select(Decision).where(
        Decision.user_id == context.user_id)
        .order_by(Decision.created_at.desc()).limit(50)))
    if len(decisions) >= 6:
        resp = provider.complete(
            db, user_id=context.user_id, agent="orchestrator",
            task="classification", system=PLAYBOOK_SYSTEM,
            prompt=json.dumps({
                "decisions": [{"action": d.action_key, "by": d.decided_by,
                               "verdict": d.verdict,
                               "detail": {k: str(v)[:80] for k, v in
                                          (d.payload or {}).items()}}
                              for d in decisions],
                "recent_events": context.recent_events[:30],
                "existing_playbooks": [
                    {"slug": f.key, "when": (f.value or {}).get("when", "")}
                    for f in db.scalars(select(UserFact).where(
                        UserFact.user_id == context.user_id,
                        UserFact.domain == "playbooks"))],
            }, sort_keys=True, default=str),
            schema=PLAYBOOK_SCHEMA,
        )
        if not (resp.stubbed or resp.refused):
            try:
                books = json.loads(resp.text).get("playbooks", [])[:2]
            except json.JSONDecodeError:
                books = []
            for b in books:
                slug = str(b["slug"])[:40].strip().lower() or "unnamed"
                try:
                    write_fact(db, user_id=context.user_id, domain="playbooks",
                               key=slug,
                               value={"when": str(b["when"])[:200],
                                      "how": str(b["how"])[:400]},
                               confidence=min(0.9, max(0.4, float(b["confidence"]))),
                               source_agent="orchestrator")
                    report["playbooks"] += 1
                except (ValueError, TypeError):
                    continue  # oversized or malformed playbook: drop it
        # Keep the shelf small: lowest-confidence beyond 8 falls off.
        shelf = list(db.scalars(select(UserFact).where(
            UserFact.user_id == context.user_id,
            UserFact.domain == "playbooks")))
        if len(shelf) > 8:
            for f in sorted(shelf, key=lambda f: (f.confidence, f.learned_at))[:len(shelf) - 8]:
                db.delete(f)

    # 3. Prune long-settled mail by blanking the heavy text — the row (and
    # its gmail_msg_id) stays, so backfill dedupe never re-ingests it; the
    # gist already lives in memory_chunks.
    cutoff = datetime.now(timezone.utc) - timedelta(days=21)
    old_rows = list(db.scalars(select(InboxMessage).where(
        InboxMessage.user_id == context.user_id,
        InboxMessage.tier.in_(("cleared", "receipt")),
        InboxMessage.body_text != "",
        InboxMessage.created_at < cutoff).limit(200)))
    for m in old_rows:
        m.body_text = ""
    report["pruned_messages"] = len(old_rows)

    result.event_writes.append(EventWrite(type="dream", payload=report))
    return report


def orchestrator_think(db: Session, *, trigger: dict, context: ContextSlice,
                       run_id: str) -> ThinkResult:
    result = ThinkResult()
    if trigger.get("kind") == "heartbeat":
        return _heartbeat(db, context, result)
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
    try:
        dream = _dream(db, context, result)
    except Exception as exc:  # noqa: BLE001 — dreaming must never kill the night
        dream = {"error": str(exc)[:200]}
        result.event_writes.append(EventWrite(type="dream", payload=dream))
    result.event_writes.append(EventWrite(
        type="reflection_run", payload={"remembered": remembered, "decayed": decayed,
                                        "dream": dream}))

    if trigger.get("kind") == "morning":
        send_push(db, user_id=context.user_id, title="Nano — your morning",
                  body=brief[:170], agent="orchestrator")
    return result


def orchestrator_render(context: ContextSlice) -> Screen:
    return Screen(title="Nano", theme="dark", sections=[])  # no screen; think-only


register_agent("orchestrator", render=orchestrator_render, think=orchestrator_think,
               slow_think=True)
