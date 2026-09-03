"""The people graph — who, not just what.

Every human correspondent gets one living profile: relationship, how the
user talks to them, a rolling summary, and a small set of dated atomic
facts. Updated incrementally on every email in or out (extract -> update,
the Mem0 pattern); consolidation happens in the update call itself — the
model returns the merged profile, superseding stale facts instead of
appending forever. Composers read it to write relationship-aware mail;
episodic detail stays in memory_chunks (hybrid recall).
"""
import json
import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .llm.provider import LLMProvider
from .models import Person, utcnow

_NOISE_RE = re.compile(
    r"no-?reply|notifications?@|mailer|newsletter|digest|updates?@|info@"
    r"|support@|billing@|receipts?@|hello@|team@|news@|noreply|do-not-reply",
    re.I)

PERSON_SYSTEM = (
    "You maintain one person's profile in a personal assistant's memory. "
    "Given the current profile and one new email (to or from them), return "
    "the UPDATED profile: relationship (who they are to the user, e.g. "
    "'sister', 'recruiter at Halcyon', 'landlord'), tone (how the user and "
    "this person write to each other — formality, warmth, quirks), summary "
    "(a rolling 3-5 sentence narrative of the relationship and what is "
    "currently going on between them), and facts (at most 8 short atomic "
    "facts worth remembering, each with a rough date, e.g. 'asked about "
    "lease renewal (Sep 2026)'). MERGE, don't append: keep what still "
    "matters, drop or supersede stale facts, never invent anything not in "
    "the profile or the email. Unknown fields stay empty strings."
)
PERSON_SCHEMA = {
    "type": "object",
    "properties": {
        "relationship": {"type": "string"},
        "tone": {"type": "string"},
        "summary": {"type": "string"},
        "facts": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["relationship", "tone", "summary", "facts"],
    "additionalProperties": False,
}


def is_human_sender(addr: str) -> bool:
    return bool(addr and "@" in addr and not _NOISE_RE.search(addr))


def get_person(db: Session, user_id: str, email: str) -> Person | None:
    return db.scalar(select(Person).where(
        Person.user_id == user_id, Person.email == email.lower().strip()))


def update_person(db: Session, provider: LLMProvider, user_id: str, *,
                  email: str, name: str = "", direction: str, subject: str,
                  body: str) -> Person | None:
    """direction: 'from_them' | 'user_wrote'. Upserts the row always;
    enriches via LLM when live (stub mode keeps counts honest, nothing more)."""
    addr = email.lower().strip()
    if not is_human_sender(addr):
        return None
    person = get_person(db, user_id, addr)
    if person is None:
        person = Person(user_id=user_id, email=addr, name=name[:200])
        db.add(person)
        db.flush()
    if name and not person.name:
        person.name = name[:200]
    person.email_count += 1
    person.last_seen = utcnow()
    person.updated_at = utcnow()

    resp = provider.complete(
        db, user_id=user_id, agent="inbox", task="person_update",
        system=PERSON_SYSTEM,
        prompt=json.dumps({
            "person": {"email": addr, "name": person.name,
                       "relationship": person.relationship, "tone": person.tone,
                       "summary": person.summary, "facts": person.facts or []},
            "new_email": {
                "direction": ("they wrote to the user" if direction == "from_them"
                              else "the user wrote to them"),
                "date": datetime.now(timezone.utc).date().isoformat(),
                "subject": subject[:200], "body": body[:4000],
            },
        }, sort_keys=True),
        schema=PERSON_SCHEMA, effort="low",
    )
    if resp.stubbed or resp.refused:
        return person
    try:
        parsed = json.loads(resp.text)
    except json.JSONDecodeError:
        return person
    person.relationship = str(parsed.get("relationship", ""))[:120]
    person.tone = str(parsed.get("tone", ""))[:250]
    person.summary = str(parsed.get("summary", ""))[:1500]
    person.facts = [str(f)[:200] for f in (parsed.get("facts") or [])][:8]
    return person


def people_for_voice(db: Session, user_id: str, limit: int = 5) -> list[dict]:
    """The composer's grounding: who the user knows, freshest first."""
    rows = db.scalars(select(Person).where(Person.user_id == user_id)
                      .order_by(Person.last_seen.desc()).limit(limit))
    return [{
        "email": p.email, "name": p.name, "relationship": p.relationship,
        "tone": p.tone, "summary": p.summary, "facts": (p.facts or [])[:8],
    } for p in rows]
