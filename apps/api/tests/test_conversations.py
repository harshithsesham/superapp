"""Conversations are remembered — on every surface, without a clean goodbye.

The bug these cover: Nano learned from the interview and from email, but not
from being talked to. The orb stored a transcript only when the model chose
`end_conversation`, realtime voice stored nothing, Telegram stored to RAM. So
"email my father" — said out loud, never sent — left no trace, and the next
conversation had never heard of him.
"""
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

# One database for the whole suite. This module used to stand up its own engine
# and repoint `superapp.db`, which worked only while it happened to be imported
# last: once a sibling module imported test_spine first, the app was bound to
# one database and test_spine's SessionLocal read another, and 42 tests that
# have nothing to do with conversations failed. Borrowing the engine is the
# convention every other module here already follows.
from test_spine import SessionLocal

from superapp.conversations import (EXTRACT_RETRY_HOURS, FACT_PREFIX, IDLE_MINUTES,
                                    conversation_key, forget_conversations,
                                    record_turns, settle, settle_idle)
from superapp.llm.provider import LLMResponse
from superapp.models import Conversation, Person, UserFact
from superapp.substrate import get_context, read_facts


class FakeProvider:
    """A provider that answers with one fixed extraction, so the wiring is
    under test rather than the model."""

    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = 0

    def complete(self, db, **kwargs):
        self.calls += 1
        return LLMResponse(text=json.dumps(self.payload), model="fake",
                           input_tokens=0, output_tokens=0)


def _turns(*pairs):
    return [{"role": r, "text": t} for r, t in pairs]


# --- the durable floor -----------------------------------------------------

def test_every_turn_is_stored_without_a_goodbye():
    """The core regression: no sign-off, no `end_conversation`, still stored."""
    db = SessionLocal()
    convo = record_turns(db, user_id="u-floor", surface="realtime",
                         turns=_turns(("user", "can you email my father"),
                                      ("nano", "what should it say?")))
    db.commit()
    assert convo is not None
    assert len(convo.turns) == 2
    assert convo.settled_at is None  # stored now, distilled later
    db.close()


def test_replay_grows_one_conversation_instead_of_making_many():
    """Clients resend the whole transcript each turn. That must overwrite one
    row, not accumulate a row per turn."""
    db = SessionLocal()
    a = record_turns(db, user_id="u-grow", surface="orb",
                     turns=_turns(("user", "hello there Nano")))
    b = record_turns(db, user_id="u-grow", surface="orb",
                     turns=_turns(("user", "hello there Nano"),
                                  ("nano", "hi"), ("user", "email my father")))
    db.commit()
    assert a.id == b.id
    assert len(b.turns) == 3
    rows = db.scalars(select(Conversation).where(Conversation.user_id == "u-grow")).all()
    assert len(rows) == 1
    db.close()


def test_a_short_replay_never_shrinks_the_record():
    """Telegram's deque trims itself. A trimmed replay must not delete turns we
    already hold."""
    db = SessionLocal()
    full = record_turns(db, user_id="u-trim", surface="telegram",
                        turns=_turns(("user", "one"), ("nano", "two"), ("user", "three")))
    # Same opening turn, so this resolves to the same conversation — which is
    # the only way the guard is reached at all.
    same = record_turns(db, user_id="u-trim", surface="telegram",
                        turns=_turns(("user", "one")))
    db.commit()
    assert same.id == full.id
    assert len(full.turns) == 3
    db.close()


def test_settled_conversation_is_never_reopened():
    """Two conversations can open with the same words. The second must not
    overwrite the first, which is already distilled."""
    db = SessionLocal()
    first = record_turns(db, user_id="u-reopen", surface="orb",
                         turns=_turns(("user", "hey")))
    settle(db, first)
    db.commit()
    second = record_turns(db, user_id="u-reopen", surface="orb",
                          turns=_turns(("user", "hey")))
    db.commit()
    assert second.id != first.id
    assert conversation_key(_turns(("user", "hey"))) == first.external_id
    db.close()


def test_a_cold_conversation_starts_a_new_one():
    db = SessionLocal()
    old = record_turns(db, user_id="u-cold", surface="orb",
                       turns=_turns(("user", "same opening line")))
    old.last_turn_at = datetime.now(timezone.utc) - timedelta(minutes=IDLE_MINUTES + 1)
    db.commit()
    fresh = record_turns(db, user_id="u-cold", surface="orb",
                         turns=_turns(("user", "same opening line")))
    db.commit()
    assert fresh.id != old.id
    db.close()


# --- the sweep -------------------------------------------------------------

def test_sweep_settles_only_quiet_conversations():
    db = SessionLocal()
    # The sweep is global by design. Drain what earlier tests left lying about
    # so this one counts its own rows and nobody else's.
    settle_idle(db)
    db.commit()

    quiet = record_turns(db, user_id="u-sweep", surface="realtime",
                         turns=_turns(("user", "a thing I said a while ago")))
    live = record_turns(db, user_id="u-sweep", surface="orb",
                        turns=_turns(("user", "something I am still saying")))
    quiet.last_turn_at = datetime.now(timezone.utc) - timedelta(minutes=IDLE_MINUTES + 1)
    db.commit()

    assert settle_idle(db) == 1
    db.commit()
    assert quiet.settled_at is not None
    assert live.settled_at is None      # still talking; leave it alone
    assert settle_idle(db) == 0         # and settling is once, not every tick
    db.close()


def test_settle_is_idempotent():
    db = SessionLocal()
    convo = record_turns(db, user_id="u-once", surface="orb",
                         turns=_turns(("user", "hello")))
    assert settle(db, convo)["settled"] is True
    assert settle(db, convo)["settled"] is False
    db.commit()
    db.close()


# --- what it learns --------------------------------------------------------

def test_conversation_teaches_nano_who_your_father_is():
    """The scenario end to end: say it out loud, never send the email, and have
    it known in the NEXT conversation."""
    db = SessionLocal()
    provider = FakeProvider({
        "facts": [{"key": "father", "belief": "Their father is Raj, who prefers a phone call to email.",
                   "confidence": 0.8}],
        "people": [{"email": "raj@example.com", "name": "Raj",
                    "relationship": "father"}],
    })
    convo = record_turns(db, user_id="u-father", surface="realtime", turns=_turns(
        ("user", "I want to email my father about the trip next month"),
        ("nano", "What's his address?"),
        ("user", "raj@example.com — he's my dad, though he'd rather I just called him"),
    ))
    result = settle(db, convo, provider)
    db.commit()

    assert result["facts"] == 1 and result["people"] == 1

    # The belief is in the fact store, namespaced by where it came from...
    facts = read_facts(db, user_id="u-father", domains=["identity"], limit=10)
    keys = {f.key for f in facts}
    assert f"{FACT_PREFIX}father" in keys
    assert "Raj" in next(f.value["text"] for f in facts if f.key == f"{FACT_PREFIX}father")

    # ...the address is bound to the relationship in the people graph...
    person = db.scalar(select(Person).where(Person.email == "raj@example.com"))
    assert person is not None and person.relationship == "father"

    # ...and the next conversation is grounded in both — including the inbox
    # composer, whose scope covers identity, which is what makes a draft to him
    # read like a draft to a father.
    context = get_context(db, agent="inbox", user_id="u-father")
    assert any(f["key"] == f"{FACT_PREFIX}father" for f in context.facts)
    db.close()


def test_extraction_cannot_clobber_the_interview_profile():
    """The interview owns the bare identity keys. Speech is namespaced so a
    stray extraction can never overwrite a thirty-minute distillation."""
    db = SessionLocal()
    from superapp.substrate import write_fact

    write_fact(db, user_id="u-clash", domain="identity", key="key_people",
               value={"text": "the interview's careful answer"}, confidence=0.9,
               source_agent="interviewer")
    convo = record_turns(db, user_id="u-clash", surface="orb", turns=_turns(
        ("user", "my key people are basically just my flatmate these days"),
        ("nano", "noted"),
        ("user", "yes, that is the whole list, nobody else worth mentioning"),
    ))
    settle(db, convo, FakeProvider({
        "facts": [{"key": "key_people", "belief": "an offhand remark", "confidence": 0.7}],
        "people": [],
    }))
    db.commit()

    kept = db.scalar(select(UserFact).where(UserFact.user_id == "u-clash",
                                            UserFact.key == "key_people"))
    assert kept.value["text"] == "the interview's careful answer"
    assert kept.source_agent == "interviewer"
    db.close()


def test_people_without_an_address_are_not_invented():
    """A relationship with no address cannot become a people row — that table
    is keyed by address, and a blank one would collide with the next stranger."""
    db = SessionLocal()
    convo = record_turns(db, user_id="u-noaddr", surface="orb", turns=_turns(
        ("user", "my landlord has been chasing me about the lease renewal"),
        ("nano", "want me to draft something?"),
        ("user", "not yet, I need to think about whether we are staying"),
    ))
    settle(db, convo, FakeProvider({
        "facts": [],
        "people": [{"email": "", "name": "the landlord", "relationship": "landlord"}],
    }))
    db.commit()
    assert db.scalars(select(Person).where(Person.user_id == "u-noaddr")).all() == []
    db.close()


def test_trivial_exchanges_are_stored_but_not_distilled():
    """"What's in my inbox" is a lookup, not memory. Store it; don't spend a
    model call on it, and don't pollute recall with it."""
    db = SessionLocal()
    provider = FakeProvider({"facts": [], "people": []})
    convo = record_turns(db, user_id="u-triv", surface="orb",
                         turns=_turns(("user", "inbox?"), ("nano", "three things")))
    result = settle(db, convo, provider)
    db.commit()
    assert provider.calls == 0
    assert result["skipped"]
    assert db.get(Conversation, convo.id) is not None  # stored all the same
    db.close()


class Exploding:
    def complete(self, db, **kwargs):
        raise RuntimeError("provider down")


def test_a_failed_extraction_is_retried_rather_than_lost():
    """An outage is not a verdict. The conversation stays unsettled so the next
    sweep tries again — the same bargain `memory.retry_pending` strikes for a
    failed embedding."""
    db = SessionLocal()
    convo = record_turns(db, user_id="u-boom", surface="orb", turns=_turns(
        ("user", "here is a long thing about my life and what I care about"),
        ("nano", "go on"),
        ("user", "and here is a second long thing that should have been learned"),
    ))
    result = settle(db, convo, Exploding())
    db.commit()
    assert result["settled"] is False
    assert convo.settled_at is None
    assert len(db.get(Conversation, convo.id).turns) == 3

    # ...and the retry lands the facts it missed.
    provider = FakeProvider({"facts": [{"key": "life", "belief": "They care about it.",
                                        "confidence": 0.7}], "people": []})
    assert settle(db, convo, provider)["facts"] == 1
    db.commit()
    db.close()


def test_a_conversation_that_always_fails_stops_being_retried():
    """Bounded, so one poisonous row cannot occupy the sweep forever."""
    db = SessionLocal()
    convo = record_turns(db, user_id="u-poison", surface="orb", turns=_turns(
        ("user", "here is a long thing about my life and what I care about"),
        ("nano", "go on"),
        ("user", "and here is a second long thing that should have been learned"),
    ))
    convo.started_at = (datetime.now(timezone.utc)
                        - timedelta(hours=EXTRACT_RETRY_HOURS + 1))
    db.commit()
    assert settle(db, convo, Exploding())["settled"] is True
    assert convo.settled_at is not None
    db.close()


# --- forgetting reaches every copy ------------------------------------------

def test_forgetting_a_note_takes_the_whole_conversation_with_it():
    """Recording conversations gave a forgotten note somewhere new to survive.

    Redacting only the matching turn is not enough, and this is the case that
    proves it: Nano's reply never contains the sentence being forgotten and
    gives the name away regardless. The conversation is the unit.
    """
    db = SessionLocal()
    convo = record_turns(db, user_id="u-forget", surface="orb", turns=_turns(
        ("user", "remember that my daughter is called Mira"),
        ("nano", "Noted — Mira."),          # the leak a per-turn redaction leaves
        ("user", "also I prefer aisle seats"),
    ))
    db.commit()

    removed = forget_conversations(db, user_id="u-forget",
                                   said="remember that my daughter is called Mira")
    db.commit()

    assert removed["conversations"] == 1 and removed["turns"] == 3
    assert db.get(Conversation, convo.id) is None
    db.close()


def test_forgetting_removes_beliefs_distilled_from_that_conversation():
    """A belief restates things in its own words, so no substring search finds
    it. Provenance does: the fact carries the conversation it came from."""
    db = SessionLocal()
    convo = record_turns(db, user_id="u-forget-fact", surface="orb", turns=_turns(
        ("user", "please remember that my daughter is called Mira and she is seven"),
        ("nano", "noted"),
        ("user", "yes, that is the one thing I want you to hold on to"),
    ))
    settle(db, convo, FakeProvider({
        # Note the wording: nothing here quotes the sentence being forgotten.
        "facts": [{"key": "daughter", "belief": "Their child is seven years old.",
                   "confidence": 0.8}],
        "people": [],
    }))
    db.commit()
    assert db.scalar(select(UserFact).where(
        UserFact.user_id == "u-forget-fact", UserFact.key == f"{FACT_PREFIX}daughter")) is not None

    removed = forget_conversations(
        db, user_id="u-forget-fact",
        said="please remember that my daughter is called Mira and she is seven")
    db.commit()

    assert removed["facts"] == 1
    assert db.scalar(select(UserFact).where(
        UserFact.user_id == "u-forget-fact",
        UserFact.key == f"{FACT_PREFIX}daughter")) is None
    db.close()


def test_forgetting_reaches_a_conversation_already_settled():
    """Settled means embedded and distilled — the state in which the detail is
    most reachable, and so the one that matters most to clear."""
    db = SessionLocal()
    convo = record_turns(db, user_id="u-forget-all", surface="telegram",
                         turns=_turns(("user", "my passport number is 12345")))
    settle(db, convo)
    db.commit()
    assert convo.settled_at is not None

    forget_conversations(db, user_id="u-forget-all", said="my passport number is 12345")
    db.commit()
    assert db.get(Conversation, convo.id) is None
    db.close()


def test_forgetting_leaves_other_conversations_alone():
    db = SessionLocal()
    keep = record_turns(db, user_id="u-forget-other", surface="orb",
                        turns=_turns(("user", "something entirely unrelated to it")))
    db.commit()
    removed = forget_conversations(db, user_id="u-forget-other", said="a detail never said")
    db.commit()
    assert removed["conversations"] == 0
    assert len(db.get(Conversation, keep.id).turns) == 1
    db.close()


def test_forgetting_matches_past_case_and_spacing():
    """The saved note and the stored turn are the same words, but a transcript
    keeps the person's own punctuation and case."""
    db = SessionLocal()
    convo = record_turns(db, user_id="u-forget-case", surface="orb",
                         turns=_turns(("user", "Remember   my   Gate Code Is 4417"),
                                      ("nano", "ok")))
    db.commit()
    removed = forget_conversations(db, user_id="u-forget-case",
                                   said="remember my gate code is 4417")
    db.commit()
    assert removed["conversations"] == 1
    assert db.get(Conversation, convo.id) is None
    db.close()


def test_one_persons_forget_never_touches_another():
    db = SessionLocal()
    mine = record_turns(db, user_id="u-tenant-a", surface="orb",
                        turns=_turns(("user", "the shared phrase we both said")))
    theirs = record_turns(db, user_id="u-tenant-b", surface="orb",
                          turns=_turns(("user", "the shared phrase we both said")))
    db.commit()
    forget_conversations(db, user_id="u-tenant-a", said="the shared phrase we both said")
    db.commit()
    assert db.get(Conversation, mine.id) is None
    assert db.get(Conversation, theirs.id) is not None
    db.close()


def test_forget_context_is_the_path_that_reaches_all_of_it():
    """The unit above proves the reach; this proves it is wired to the thing a
    person actually says. "Forget that" goes through `forget_context`, and it
    must clear the transcript as well as the note it was written into."""
    from superapp.context_notes import forget_context, save_context
    from superapp.models import SavedContext

    said = "remember that the spare key is under the third pot"
    db = SessionLocal()
    note = save_context(db, user_id="u-forget-e2e", text=said)
    convo = record_turns(db, user_id="u-forget-e2e", surface="orb",
                         turns=_turns(("user", said), ("nano", "Got it.")))
    db.commit()

    assert forget_context(db, user_id="u-forget-e2e", note_id=note.id) is True
    db.commit()

    assert db.get(SavedContext, note.id) is None       # the note itself
    remaining = db.get(Conversation, convo.id)
    kept = [t["text"] for t in (remaining.turns if remaining else [])]
    assert said not in kept, "the transcript copy must go with the note"
    assert remaining is None, "the exchange that carried it goes whole"
    db.close()
