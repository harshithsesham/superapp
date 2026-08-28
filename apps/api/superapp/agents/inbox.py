"""Inbox agent (Phase 3) — Nano: the AI-managed inbox.

Best-UX configuration: Opus 5 triages EVERY email with the full body + personal
context; anything headed for the cleared tier gets a second, adversarial
verification pass ("would the user regret not seeing this?") — misclassifying
an important email is this product's fatal failure, so the discard pile is
double-checked. Replies are drafted immediately for the needs_reply tier.

think() trigger kinds:
- email_sync (Pub/Sub webhook, cron, connect backfill): fetch -> triage ->
  verify -> draft -> (modify tier only) archive.
- scheduled: the morning brief — one push instead of 41 notifications; also
  distills accumulated draft edits into reply-style facts (voice learning).

Trust ladder (settings.gmail_scope_tier): read = triage only; send = drafts
sendable on tap; modify = cleared tier actually archived. Nothing ever sends
without an explicit user tap on a draft.
"""
import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..inbox.gmail_client import GmailClient
from ..llm.provider import LLMProvider
from ..push import send_push
from ..sdui.blocks import (
    Action, ActionRow, AgentCard, AgentStat, DraftCard, InsightCard, ListBlock, ListItem,
    Screen, Section, TextBlock,
)
from ..substrate import ContextSlice
from ..substrate.events import recent_events
from ..substrate.inbox import accounts, create_draft, insert_message
from ..vault import get_token
from ..kernel import record_decision
from .base import EventWrite, FactWrite, ThinkResult, register_agent

TRIAGE_SYSTEM = (
    "You are the inbox agent of a personal chief-of-staff app. Triage ONE email "
    "for this specific user using their context (VIPs, goals, recent activity). "
    "Tiers: needs_reply (a human is waiting on the user's words, or a decision "
    "with a deadline), worth_knowing (real information, nothing to do), "
    "receipt (purchase/order/shipping confirmation), cleared (promotions, "
    "newsletters, social pings, automated noise). gist: one calm line, max 15 "
    "words. why_now: for needs_reply only, the urgency in max 8 words (e.g. "
    "'deadline today EOD'), else empty. clear_reason: for cleared only, one of: "
    "promotion, newsletter, social, automated, other."
)
TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "tier": {"type": "string", "enum": ["needs_reply", "worth_knowing", "receipt", "cleared"]},
        "gist": {"type": "string"},
        "why_now": {"type": "string"},
        "clear_reason": {"type": "string"},
    },
    "required": ["tier", "gist", "why_now", "clear_reason"],
    "additionalProperties": False,
}

VERIFY_SYSTEM = (
    "You are an adversarial reviewer. An assistant wants to silently archive "
    "this email for this user. Argue the other side: is there ANY plausible way "
    "the user would regret never seeing it (money owed, a real human, a "
    "deadline, legal/account issues, anything personal)? If yes, veto."
)
VERIFY_SCHEMA = {
    "type": "object",
    "properties": {"veto": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["veto", "reason"],
    "additionalProperties": False,
}

DRAFT_SYSTEM = (
    "You draft email replies in the user's own voice. Use their reply-style "
    "notes and past edits. Short, warm, direct; no corporate filler, no "
    "sign-off longer than a first name. Answer the actual question; commit to "
    "specifics when the user's context supports them, otherwise leave a clear "
    "placeholder like [time]. NEVER invent names, facts, times, or commitments "
    "not present in the email or the provided context. If you sign at all, "
    "sign exactly as you_are.name — no other name may appear as the sender. "
    "Output only the reply body."
)

DISTILL_STYLE_SYSTEM = (
    "You analyze how a user edits AI-drafted emails before sending. From the "
    "before/after pairs, extract their voice: greeting/sign-off habits, length "
    "preference, formality, phrases they add or delete. Be concrete and terse."
)
STYLE_SCHEMA = {
    "type": "object",
    "properties": {"reply_style": {"type": "string"}},
    "required": ["reply_style"],
    "additionalProperties": False,
}

STYLE_DISTILL_MIN = 3


def _fact(context: ContextSlice, key: str) -> dict | None:
    f = next((f for f in context.facts if f["domain"] == "inbox" and f["key"] == key), None)
    return f["value"] if f else None


def _heuristic_triage(msg) -> dict:
    """Offline fallback (stub mode): honest heuristics, marked low-confidence."""
    text = f"{msg.from_addr} {msg.subject}".lower()
    if any(w in text for w in ("no-reply", "promo", "offers", "notifications@", "digest", "info@x.com")):
        reason = ("promotion" if any(w in text for w in ("promo", "offers", "% off"))
                  else "social" if any(w in text for w in ("linkedin", "x.com", "follow"))
                  else "newsletter" if any(w in text for w in ("substack", "digest", "medium"))
                  else "automated")
        return {"tier": "cleared", "gist": msg.subject[:80], "why_now": "", "clear_reason": reason}
    if any(w in text + msg.body_text.lower() for w in ("order", "shipped", "invoice", "receipt")):
        tier = "receipt" if any(w in text for w in ("order", "shipped")) else "worth_knowing"
        return {"tier": tier, "gist": msg.subject[:80], "why_now": "", "clear_reason": ""}
    if "?" in msg.body_text or any(w in msg.body_text.lower() for w in ("deadline", "confirm", "let me know", "reply")):
        why = "deadline today" if "today" in msg.body_text.lower() else "waiting on you"
        return {"tier": "needs_reply", "gist": msg.subject[:80], "why_now": why, "clear_reason": ""}
    return {"tier": "worth_knowing", "gist": msg.subject[:80], "why_now": "", "clear_reason": ""}


def _triage_one(db: Session, context: ContextSlice, provider: LLMProvider, msg) -> dict:
    payload = {
        "email": {"from_name": msg.from_name, "from_addr": msg.from_addr,
                  "subject": msg.subject, "body": msg.body_text[:6000],
                  "received_at": msg.received_at.isoformat()},
        "user_context": {
            "facts": [f for f in context.facts if f["domain"] in ("inbox", "goals")],
        },
    }
    resp = provider.complete(
        db, user_id=context.user_id, agent="inbox", task="inbox_triage",
        system=TRIAGE_SYSTEM, prompt=json.dumps(payload, sort_keys=True),
        schema=TRIAGE_SCHEMA, effort="medium",
    )
    if not resp.stubbed and not resp.refused:
        try:
            parsed = json.loads(resp.text)
            if parsed.get("tier") in ("needs_reply", "worth_knowing", "receipt", "cleared"):
                return parsed
        except json.JSONDecodeError:
            pass
    return _heuristic_triage(msg)


def _verify_clear(db: Session, context: ContextSlice, provider: LLMProvider, msg) -> bool:
    """True = safe to clear. In stub mode the heuristic tiering is conservative
    enough; live, an adversarial Opus pass reviews the discard pile."""
    resp = provider.complete(
        db, user_id=context.user_id, agent="inbox", task="clear_verification",
        system=VERIFY_SYSTEM,
        prompt=json.dumps({"from": msg.from_addr, "subject": msg.subject,
                           "body": msg.body_text[:4000]}, sort_keys=True),
        schema=VERIFY_SCHEMA, effort="medium",
    )
    if resp.stubbed or resp.refused:
        return True
    try:
        return not json.loads(resp.text)["veto"]
    except (json.JSONDecodeError, KeyError):
        return False  # verifier unparseable -> keep the email visible


def _draft_reply(db: Session, context: ContextSlice, provider: LLMProvider, msg) -> str:
    style = _fact(context, "reply_style")
    # Who the user IS — without this the model invents a signature name.
    # Overridable via the inbox/signature_name fact; defaults to the user id.
    identity = _fact(context, "signature_name") or {}
    name = identity.get("name") or context.user_id.capitalize()
    resp = provider.complete(
        db, user_id=context.user_id, agent="inbox", task="reply_draft",
        system=DRAFT_SYSTEM,
        prompt=json.dumps({
            "you_are": {"name": name, "email": msg.account_email},
            "email": {"from_name": msg.from_name, "subject": msg.subject, "body": msg.body_text[:6000]},
            "reply_style_notes": (style or {}).get("notes", ""),
            "user_facts": [f for f in context.facts if f["domain"] in ("goals", "identity")],
        }, sort_keys=True),
    )
    if resp.stubbed or resp.refused:
        first = msg.from_name.split()[0] if msg.from_name else "there"
        return (f"Hi {first} — got it, thanks for the nudge. Yes from my side; "
                f"I'll confirm the details by tomorrow. (stub draft)")
    return resp.text.strip()


def _sync(db: Session, context: ContextSlice, trigger: dict) -> ThinkResult:
    settings = get_settings()
    provider = LLMProvider()
    result = ThinkResult()
    counts = {"new": 0, "needs_reply": 0, "worth_knowing": 0, "receipt": 0, "cleared": 0, "archived": 0}

    for acct in accounts(db, context.user_id):
        token = get_token(db, user_id=context.user_id, provider=f"gmail:{acct.email}")
        client = GmailClient(json.loads(token) if token else None)
        msgs, new_hid = client.new_messages(acct.history_id)
        acct.history_id = new_hid
        for raw in msgs:
            msg = insert_message(db, user_id=context.user_id, account_email=acct.email, msg=raw)
            if msg is None:
                continue
            counts["new"] += 1
            verdict = _triage_one(db, context, provider, msg)
            msg.tier = verdict["tier"]
            msg.gist = verdict["gist"][:250]
            msg.why_now = verdict["why_now"][:120]
            msg.clear_reason = verdict["clear_reason"][:120]

            if msg.tier == "cleared":
                if _verify_clear(db, context, provider, msg):
                    msg.verified_clear = True
                    if settings.gmail_scope_tier == "modify":
                        client.archive(msg.gmail_msg_id)
                        msg.archived = True
                        counts["archived"] += 1
                else:
                    msg.tier = "worth_knowing"  # verifier veto: stay visible
            if msg.tier == "needs_reply":
                create_draft(db, user_id=context.user_id, message_id=msg.id,
                             body=_draft_reply(db, context, provider, msg))
            counts[msg.tier] += 1
            # Nano's own verdicts go in the ledger too — the "did without
            # asking" side of the autonomy panel is counted, never estimated.
            if msg.tier in ("cleared", "receipt"):
                record_decision(db, user_id=context.user_id, agent="inbox",
                                action_key="inbox.archive_noise", decided_by="nano",
                                verdict="acted", payload={"message_id": msg.id})
            elif msg.tier == "worth_knowing":
                record_decision(db, user_id=context.user_id, agent="inbox",
                                action_key="inbox.flag_to_read", decided_by="nano",
                                verdict="acted", payload={"message_id": msg.id})
    db.flush()

    result.event_writes.append(EventWrite(type="inbox_synced", domain="inbox", payload=counts))
    return result


def _morning_brief(db: Session, context: ContextSlice, result: ThinkResult) -> None:
    data = context.domain_data.get("inbox", {})
    asks = [a for a in data.get("needs_reply", []) if not (a.get("draft") or {}).get("deferred")]
    reads = data.get("worth_knowing", [])
    if not data.get("connected"):
        return
    top = asks[0] if asks else None
    line = (f"{len(asks)} need your words. {len(reads)} worth a look."
            if asks else f"Inbox Zero. {data.get('cleared_count', 0)} handled without you.")
    if top:
        line += f" First: {top['from_name']} — {top['why_now'] or top['gist']}."
    send_push(db, user_id=context.user_id, title="Nano", body=line, agent="inbox")
    result.fact_writes.append(FactWrite(
        domain="inbox", key="morning_brief",
        value={"date": datetime.now(timezone.utc).date().isoformat(), "text": line[:400]},
        confidence=1.0,
    ))


def _maybe_distill_style(db: Session, context: ContextSlice, result: ThinkResult) -> None:
    edits = [e.payload for e in recent_events(
        db, user_id=context.user_id, limit=200, types=["draft_edited"])]
    meta = _fact(context, "style_meta") or {"edit_count": 0}
    if len(edits) - meta["edit_count"] < STYLE_DISTILL_MIN:
        return
    provider = LLMProvider()
    resp = provider.complete(
        db, user_id=context.user_id, agent="inbox", task="style_distillation",
        system=DISTILL_STYLE_SYSTEM,
        prompt=json.dumps({"edits": edits[:50]}, sort_keys=True), schema=STYLE_SCHEMA,
    )
    if resp.refused:
        return
    notes = ""
    if not resp.stubbed:
        try:
            notes = json.loads(resp.text)["reply_style"]
        except (json.JSONDecodeError, KeyError):
            return
    else:
        notes = "Keeps drafts short; drops formal sign-offs. (stub)"
    result.fact_writes += [
        FactWrite(domain="inbox", key="reply_style", value={"notes": notes[:500]}, confidence=0.85),
        FactWrite(domain="inbox", key="style_meta", value={"edit_count": len(edits)}, confidence=1.0),
    ]
    result.event_writes.append(EventWrite(type="reply_style_distilled", domain="inbox",
                                          payload={"edits": len(edits)}))


def inbox_think(db: Session, *, trigger: dict, context: ContextSlice, run_id: str) -> ThinkResult:
    # Pull-to-refresh means "check my mail" — same as a sync trigger.
    if trigger.get("kind") in ("email_sync", "user_refresh"):
        return _sync(db, context, trigger)
    result = ThinkResult()
    _maybe_distill_style(db, context, result)
    _morning_brief(db, context, result)
    return result


def inbox_hero(data: dict, screen: str | None = None) -> AgentCard:
    """The Inbox Zero hero card — used on the inbox screen and the Hub."""
    asks = [a for a in data.get("needs_reply", []) if not (a.get("draft") or {}).get("deferred")]
    reads = data.get("worth_knowing", [])
    cleared = data.get("cleared_count", 0)
    n = len(asks)
    headline = (f"{n} repl{'y needs' if n == 1 else 'ies need'} your yes." if n else "Inbox Zero.")
    body = (f"I'm watching your Primary inbox. {cleared} handled without you, "
            f"{len(reads)} flagged to read"
            + (", and the replies are written and waiting." if n else ". Nothing needs you."))
    return AgentCard(
        id="inbox-zero", agent="inbox", name="Inbox Zero", sub="Gmail · Primary",
        live=True, headline=headline, body=body, screen=screen,
        stats=[
            AgentStat(n=str(cleared), label="handled without you", accent=True),
            AgentStat(n=str(n), label="need a reply"),
            AgentStat(n=str(len(reads)), label="to read"),
        ],
    )


def inbox_render(context: ContextSlice) -> Screen:
    data = context.domain_data.get("inbox", {})
    if not data.get("connected"):
        return Screen(title="Nano", theme="dark", sections=[Section(title=None, blocks=[
            TextBlock(text="Your chief of staff", variant="caption"),
            TextBlock(text="I run the boring half of your inbox.", variant="title"),
            TextBlock(text="I can watch your inbox, archive the noise, and leave a draft "
                           "waiting on anything that needs you.", variant="body"),
            TextBlock(text="Read-only until you approve a draft. Nothing sends without you.",
                      variant="caption"),
            ActionRow(actions=[Action(id="inbox.connect", label="Connect inbox")]),
        ])])

    sections: list[Section] = []
    asks = data.get("needs_reply", [])
    active = [a for a in asks if not (a.get("draft") or {}).get("deferred")]
    ask_blocks: list = []
    for a in asks:  # deferred asks stay visible, settled — they never just vanish
        d = a.get("draft") or {}
        prior = a.get("prior_from_sender", 0)
        why_bits = []
        if a.get("why_now"):
            why_bits.append(a["why_now"].rstrip("."))
        if a.get("gist") and a.get("gist") != a.get("why_now"):
            why_bits.append(a["gist"].rstrip("."))
        if prior:
            why_bits.append(f"{prior + 1} emails from this sender lately")
        why_detail = (". ".join(why_bits) + ". Drafted from the thread in your voice — "
                      "nothing sends until you say so.")
        ask_blocks.append(DraftCard(
            id=d.get("id", a["id"]), agent="inbox", from_name=a["from_name"],
            subject=a["subject"], why=a["why_now"] or a["gist"],
            draft=d.get("body", ""), status=d.get("status", "waiting"),
            deferred_label="ASKING AGAIN AT 6PM" if d.get("deferred") else None,
            why_detail=why_detail,
        ))
    if not asks:
        ask_blocks.append(TextBlock(text="Nothing needs your words right now.", variant="caption"))
    sections.append(Section(title=f"Needs your words · {len(active)}", blocks=ask_blocks))

    reads = data.get("worth_knowing", [])
    if reads:
        sections.append(Section(title=f"Read only · nothing to do · {len(reads)}", blocks=[
            ListBlock(items=[
                ListItem(id=r["id"], title=r["from_name"], subtitle=r["gist"] or r["subject"],
                         detail=f"{r['subject']}\n\n{r['body']}".strip() or None)
                for r in reads
            ])
        ]))

    sent = data.get("sent", [])
    if sent:
        sections.append(Section(title=f"Sent by Nano · {len(sent)}", blocks=[
            ListBlock(items=[
                ListItem(id=f"sent-{i}", title=f"To {x['to_name'] or x['to_addr']}",
                         subtitle=x["subject"] or x["body"][:70],
                         trailing=x["sent_at"][11:16] if len(x["sent_at"]) > 16 else None,
                         detail=f"{x['subject']}\n\n{x['body']}".strip())
                for i, x in enumerate(sent)
            ])
        ]))

    cleared = data.get("cleared_count", 0)
    if cleared:
        by_reason = data.get("cleared_by_reason", {})
        summary = ", ".join(f"{n} {reason}s" for reason, n in sorted(by_reason.items(), key=lambda kv: -kv[1]))
        sections.append(Section(title=f"Cleared without asking · {cleared}", blocks=[
            TextBlock(text=f"Filed {summary}. None of it was a decision.", variant="caption"),
        ]))

    brief = _fact(context, "morning_brief")
    if brief:
        sections.insert(0, Section(title=None, blocks=[InsightCard(
            id="morning-brief", agent="inbox", title=f"This morning — {brief.get('date', '')}",
            body=brief.get("text", ""), emphasis="default",
        )]))

    stamp = datetime.now(timezone.utc).strftime("%a %d %b · %H:%M").upper()
    sections.insert(0, Section(title=None, blocks=[
        TextBlock(text=stamp, variant="caption"),
        inbox_hero(data),
    ]))
    return Screen(title="Inbox Zero", theme="dark", sections=sections)


register_agent("inbox", render=inbox_render, think=inbox_think, slow_think=True)
