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

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .llm.provider import LLMProvider
from .models import Person, utcnow

# The model's own verdict outranks the address heuristic: profiles it calls
# automated/marketing are services, not people.
_SERVICE_RE = re.compile(
    r"automat|marketing|loyalty program|notification sender|promotional"
    r"|ride[- ]hailing|newsletter|account service|drip|mailing list", re.I)

_NOISE_RE = re.compile(
    r"no[-._ ]?reply|do[-._ ]?not[-._ ]?reply|notifications?@|mailer|alerts?"
    r"|newsletter|digest|updates?@|info@|support@|billing@|receipts?@"
    r"|hello@|team@|news@|service@|accounts?@|security@|verify|automated",
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
                  body: str, occurred_at: datetime | None = None,
                  enrich: bool = True) -> Person | None:
    """direction: 'from_them' | 'user_wrote'. Upserts the row always;
    enriches via LLM when live (stub mode keeps counts honest, nothing more).

    `occurred_at` is when the email was actually sent. It matters twice. The
    profile is dated by it, so a note reads "asked about the lease (Mar 2024)"
    instead of stamping every imported year with today. And `last_seen` only
    ever moves FORWARD: importing a 2019 thread must not make a dormant contact
    look like this morning's mail, because last_seen orders who the assistant
    thinks is currently in the user's life.

    `enrich=False` records the exchange without spending a model call. A deep
    historical import passes False for the long tail — thousands of one-call
    updates would cost more than the profiles are worth, and the profile is
    better built from the recent, dense end of the record anyway.
    """
    addr = email.lower().strip()
    if not is_human_sender(addr):
        return None
    person = get_person(db, user_id, addr)
    if person is not None and _SERVICE_RE.search(person.relationship or ""):
        return None  # judged a service before; don't spend another look
    is_new = person is None
    if is_new:
        person = Person(user_id=user_id, email=addr, name=name[:200])
        db.add(person)
        db.flush()
    if name and not person.name:
        person.name = name[:200]
    person.email_count += 1
    when = occurred_at or utcnow()
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    seen = None if is_new else person.last_seen   # a fresh row defaults to now
    if seen is not None and seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    person.last_seen = when if (seen is None or when > seen) else seen
    person.updated_at = utcnow()
    if not enrich:
        return person

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
                # The email's own date. Dating an imported 2019 thread "today"
                # is how a profile fills up with facts that never happened.
                "date": when.date().isoformat(),
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
    if _SERVICE_RE.search(person.relationship) or _SERVICE_RE.search(person.summary[:200]):
        db.delete(person)
        return None
    return person


def people_for_voice(db: Session, user_id: str, limit: int = 5) -> list[dict]:
    """The composer's grounding: who the user knows, freshest first."""
    rows = db.scalars(select(Person).where(Person.user_id == user_id)
                      .order_by(Person.last_seen.desc()).limit(limit * 2))
    rows = [p for p in rows if not _SERVICE_RE.search(p.relationship or "")][:limit]
    return [_voice_shape(p) for p in rows]


def _voice_shape(p: Person) -> dict:
    return {"email": p.email, "name": p.name, "relationship": p.relationship,
            "tone": p.tone, "summary": p.summary, "facts": (p.facts or [])[:8]}


# The glue of asking for something. Relationship words — mentor, father,
# landlord — are deliberately NOT here: they are exactly what a person says
# instead of an address, and matching them is the entire point.
_NOT_A_NAME = {
    "email", "emails", "mail", "send", "sent", "write", "wrote", "draft",
    "message", "reply", "replied", "tell", "ask", "asking", "about", "the",
    "and", "for", "with", "that", "this", "them", "they", "him", "her", "his",
    "hers", "our", "you", "your", "yours", "can", "could", "would", "should",
    "please", "want", "need", "just", "let", "get", "make", "know", "say",
    "said", "back", "out", "who", "what", "when", "where", "how", "one",
    "something", "anything", "quick", "note", "line", "again", "now", "today",
    "tomorrow", "yesterday", "morning", "evening", "thanks", "thank",
}


def _named_tokens(text: str) -> list[str]:
    """Words in what the person just said that could name somebody."""
    seen: list[str] = []
    for tok in re.findall(r"[a-z][a-z']{2,}", (text or "").lower()):
        if tok not in _NOT_A_NAME and tok not in seen:
            seen.append(tok)
    return seen[:8]   # a long sentence must not become a long OR


def people_matching(db: Session, user_id: str, text: str, limit: int = 4) -> list[Person]:
    """Anyone the person just NAMED, however long ago they last wrote.

    `people_for_voice` alone answers "who wrote recently", which is the wrong
    question for "email my mentor": a mentor you last heard from in March is
    exactly the person a recency window drops. Matching is at word starts, so
    "can" does not turn Duncan into a candidate; it errs toward offering the
    model a choice, and a new recipient is hard-capped at ask-first in the
    kernel, so a wrong candidate is confirmed by a human before it is mailed.
    """
    tokens = _named_tokens(text)
    if not tokens:
        return []
    clauses = []
    for tok in tokens:
        starts, inner = f"{tok}%", f"% {tok}%"
        clauses += [
            func.lower(Person.name).like(starts), func.lower(Person.name).like(inner),
            func.lower(Person.relationship).like(starts),
            func.lower(Person.relationship).like(inner),
            func.lower(Person.email).like(starts),
        ]
    rows = db.scalars(
        select(Person).where(Person.user_id == user_id, or_(*clauses))
        .order_by(Person.last_seen.desc()).limit(limit * 3))
    return [p for p in rows if not _SERVICE_RE.search(p.relationship or "")][:limit]


def people_for_turn(db: Session, user_id: str, text: str = "",
                    recent: int = 5, named: int = 4) -> list[dict]:
    """Who to put in front of the model for THIS turn: the recent handful, plus
    anyone the person just named. Shared by the orb and realtime voice so the
    two can never drift into knowing different people."""
    out = people_for_voice(db, user_id, limit=recent)
    known = {p["email"] for p in out}
    for person in people_matching(db, user_id, text, limit=named):
        if person.email not in known:
            out.append(_voice_shape(person))
            known.add(person.email)
    return out
