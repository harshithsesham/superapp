"""The identity interview — Nano gets to know its person (north star §2).

Grounding: Park et al., "Generative Agent Simulations of 1,000 People" — a
~2-hour adaptive interview, injected as context, reproduced people's choices
at ~85% of their own test-retest consistency. The transcript IS the clone
seed; the adaptive follow-ups are where fidelity comes from, so the
interviewer is a real model with the conversation so far, not a script.

Flow: sections with goals; the model asks ONE question at a time, follows up
when an answer opens a door, moves on when a section is saturated, and closes
by writing identity facts (the distillation).
"""
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from .llm.provider import LLMProvider
from .models import InterviewSession, InterviewTurn, utcnow
from .substrate.facts import write_fact

SECTIONS = [
    ("opening", "Who they are in their own words — name, where life happens, what a normal week looks like."),
    ("routines", "Daily rhythm: mornings, meals, exercise, sleep, the recurring shape of their days."),
    ("people", "The people who matter — family, partner, closest friends, key work relationships; how they communicate with each."),
    ("work_money", "What they do, how they think about money — spending style, anxieties, goals, what's worth paying for."),
    ("style_voice", "How they communicate: tone in messages, formality, directness, humor; how they dress and want to appear."),
    ("decisions", "How they decide: what they never compromise on, what they happily delegate, past decisions they regret or are proud of."),
    ("closing", "Anything the interview missed that a chief of staff must know; their hopes for what Nano takes off their plate."),
]

INTERVIEWER_SYSTEM = (
    "You are Nano, an AI chief of staff, conducting your first long conversation "
    "with the person whose life you will help run. Your goal is to know them well "
    "enough to act as they would. Warm, curious, unhurried — a great interviewer, "
    "not a form. Ask EXACTLY ONE question at a time, conversational and short "
    "(spoken aloud, so no lists, no preamble). Follow up when an answer opens a "
    "door; go deeper on specifics (names, amounts, habits) — specifics are what "
    "make you useful. Never interrogate about traumas; follow comfort. When the "
    "current section feels covered, move on naturally."
)

QUESTION_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {"type": "string"},
        "advance_section": {"type": "boolean"},
        "interview_complete": {"type": "boolean"},
    },
    "required": ["question", "advance_section", "interview_complete"],
    "additionalProperties": False,
}

DISTILL_SYSTEM = (
    "You are distilling a long get-to-know-you interview into an AI chief of "
    "staff's core beliefs about its person. Extract only what the person "
    "actually said or clearly implied. Every field a compact string (2-3 "
    "sentences max), specific, no filler."
)
DISTILL_SCHEMA = {
    "type": "object",
    "properties": {
        "identity": {"type": "string"},        # who they are, in essence
        "routines": {"type": "string"},
        "key_people": {"type": "string"},
        "money_attitudes": {"type": "string"},
        "communication_style": {"type": "string"},
        "decision_rules": {"type": "string"},  # never-compromise + happily-delegate
        "wants_from_nano": {"type": "string"},
    },
    "required": ["identity", "routines", "key_people", "money_attitudes",
                 "communication_style", "decision_rules", "wants_from_nano"],
    "additionalProperties": False,
}

OPENER = (
    "Hi — I'm Nano. Before I start running the boring half of your life, I'd "
    "like to actually know you. This is just me getting to know you — no wrong "
    "answers, no test. All together it takes about thirty minutes, but you "
    "don't have to do it in one sitting: stop whenever you like and I'll pick "
    "up right where we left off. So, whenever you're ready — tell me about "
    "yourself. Who are you, and what does your life look like right now?"
)

RESUME_INSTRUCTION = (
    "The person just came back to continue this conversation after a break. "
    "In your own words — one warm, natural sentence, different every time, "
    "never 'welcome back, no rush' boilerplate — acknowledge them and then "
    "re-raise your open question conversationally (rephrased, not verbatim). "
    "Return it all as `question`."
)

STUB_QUESTIONS = {
    "opening": "What does a normal week look like for you?",
    "routines": "Walk me through your typical morning.",
    "people": "Who are the people you talk to most, and how?",
    "work_money": "How do you think about spending money — what's worth it, what isn't?",
    "style_voice": "How would a friend describe the way you write messages?",
    "decisions": "What's something you never compromise on?",
    "closing": "What do you most want me to take off your plate?",
}


def transcript(db: Session, session_id: str) -> list[dict]:
    turns = db.scalars(select(InterviewTurn).where(InterviewTurn.session_id == session_id)
                       .order_by(InterviewTurn.idx))
    return [{"role": t.role, "text": t.text} for t in turns]


def _add_turn(db: Session, session: InterviewSession, role: str, text: str) -> InterviewTurn:
    idx = db.scalar(select(InterviewTurn.idx).where(InterviewTurn.session_id == session.id)
                    .order_by(InterviewTurn.idx.desc())) 
    turn = InterviewTurn(session_id=session.id, idx=(idx or 0) + 1, role=role, text=text)
    db.add(turn)
    db.flush()
    return turn


def start(db: Session, user_id: str) -> tuple[InterviewSession, str, bool]:
    """Resume the active session or start fresh.
    Returns (session, question, resumed). On resume, Nano re-asks its last
    question with a welcome-back framing (stored as a fresh turn so the voice
    audio matches the words)."""
    session = db.scalar(select(InterviewSession).where(
        InterviewSession.user_id == user_id, InterviewSession.status == "active"))
    if session is not None:
        last_nano = db.scalar(select(InterviewTurn).where(
            InterviewTurn.session_id == session.id, InterviewTurn.role == "nano")
            .order_by(InterviewTurn.idx.desc()))
        has_answers = db.scalar(select(InterviewTurn.id).where(
            InterviewTurn.session_id == session.id, InterviewTurn.role == "user"))
        if last_nano and has_answers:
            text = _resume_line(db, session, last_nano.text)
            _add_turn(db, session, "nano", text)
            return session, text, True
        return session, (last_nano.text if last_nano else OPENER), False
    session = InterviewSession(user_id=user_id)
    db.add(session)
    db.flush()
    _add_turn(db, session, "nano", OPENER)
    return session, OPENER, False


def _resume_line(db: Session, session: InterviewSession, open_question: str) -> str:
    provider = LLMProvider()
    resp = provider.complete(
        db, user_id=session.user_id, agent="interviewer", task="interview_question",
        system=INTERVIEWER_SYSTEM,
        prompt=json.dumps({
            "instruction": RESUME_INSTRUCTION,
            "recent_transcript": transcript(db, session.id)[-12:],
            "open_question": open_question,
        }, sort_keys=True),
        schema=QUESTION_SCHEMA,
    )
    if not (resp.stubbed or resp.refused):
        try:
            return json.loads(resp.text)["question"]
        except (json.JSONDecodeError, KeyError):
            pass
    return "Good to have you back. So — " + open_question[0].lower() + open_question[1:]


def answer(db: Session, session: InterviewSession, user_text: str) -> tuple[str, bool, float]:
    """Record the answer, produce the next question.
    Returns (next_question_or_closing, done, progress 0..1)."""
    _add_turn(db, session, "user", user_text)

    section_idx = next((i for i, (name, _) in enumerate(SECTIONS) if name == session.section), 0)
    provider = LLMProvider()
    resp = provider.complete(
        db, user_id=session.user_id, agent="interviewer", task="interview_question",
        system=INTERVIEWER_SYSTEM,
        prompt=json.dumps({
            "sections_total": [name for name, _ in SECTIONS],
            "current_section": {"name": session.section, "goal": SECTIONS[section_idx][1]},
            "sections_remaining": [name for name, _ in SECTIONS[section_idx + 1:]],
            "conversation": transcript(db, session.id),
        }, sort_keys=True),
        schema=QUESTION_SCHEMA,
    )

    advance, complete, question = False, False, None
    if not resp.stubbed and not resp.refused:
        try:
            parsed = json.loads(resp.text)
            question = parsed["question"]
            advance = parsed["advance_section"]
            complete = parsed["interview_complete"]
        except (json.JSONDecodeError, KeyError):
            pass
    if question is None:  # offline: scripted pass through the sections
        advance = True
        nxt = section_idx + 1
        complete = nxt >= len(SECTIONS)
        question = STUB_QUESTIONS.get(SECTIONS[min(nxt, len(SECTIONS) - 1)][0], "Anything else?")

    if complete or (advance and section_idx + 1 >= len(SECTIONS)):
        closing = ("That's everything I need — thank you. Give me a moment to take "
                   "this all in, and then the app is yours. It'll feel different.")
        _add_turn(db, session, "nano", closing)
        session.status = "completed"
        session.completed_at = utcnow()
        _distill(db, session)
        return closing, True, 1.0

    if advance:
        session.section = SECTIONS[section_idx + 1][0]
        section_idx += 1
    _add_turn(db, session, "nano", question)
    return question, False, round(section_idx / len(SECTIONS), 2)


def _distill(db: Session, session: InterviewSession) -> None:
    provider = LLMProvider()
    resp = provider.complete(
        db, user_id=session.user_id, agent="interviewer", task="identity_distillation",
        system=DISTILL_SYSTEM,
        prompt=json.dumps({"interview": transcript(db, session.id)}, sort_keys=True),
        schema=DISTILL_SCHEMA,
    )
    profile: dict = {}
    if not resp.stubbed and not resp.refused:
        try:
            profile = json.loads(resp.text)
        except json.JSONDecodeError:
            profile = {}
    if not profile:  # stub: mark seeded so the flow is testable offline
        profile = {k: "(stub distillation)" for k in DISTILL_SCHEMA["required"]}

    for key, value in profile.items():
        write_fact(db, user_id=session.user_id, domain="identity", key=key,
                   value={"text": str(value)[:800]}, confidence=0.9,
                   source_agent="interviewer")
