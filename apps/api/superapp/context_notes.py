"""Save the user's actual words; indexing is retryable and never loses them."""
import uuid

from sqlalchemy import select, text, func, delete
from sqlalchemy.exc import IntegrityError

from . import memory
from .models import SavedContext, Event


def save_context(db, *, user_id: str, text: str) -> SavedContext:
    body = text.strip()
    if not body:
        raise ValueError("Nothing to remember")
    note_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"nano-context:{user_id}:{body}"))
    _lock(db, user_id)
    note = db.get(SavedContext, note_id)
    if note is None:
        try:
            with db.begin_nested():
                note = SavedContext(id=note_id, user_id=user_id, text=body)
                db.add(note)
                db.flush()
        except IntegrityError:
            note = db.get(SavedContext, note_id)
            if note is None:
                raise
    _index(db, note)
    return note


def _index(db, note) -> None:
    if note.indexed or not memory.available(db):
        return
    try:
        with db.begin_nested():
            count = memory.remember(db, user_id=note.user_id, domain="knowledge", kind="chat",
                ref_id=note.id, content=note.text, title="From our conversation", source="import",
                author="user", source_ref=f"nano:context:{note.id}", event_at=note.created_at)
            note.indexed = bool(count)
    except Exception:
        # The canonical text has already been saved. The dispatcher retries
        # a failed SQL/index write; embedding outages retain pending chunks.
        pass


def recent_context(db, user_id: str) -> list[dict]:
    return [{"id": n.id, "text": n.text, "when": n.created_at.isoformat()} for n in db.scalars(
        select(SavedContext).where(SavedContext.user_id == user_id)
        .order_by(SavedContext.created_at.desc(), SavedContext.id).limit(8))]


def _lock(db, user_id):
    # Saving, forgetting, and retry indexing share this lock, so a concurrent
    # indexer cannot recreate searchable text after a successful deletion.
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                   {"key": f"saved_context:{user_id}"})


def forget_context(db, *, user_id: str, note_id: str) -> bool:
    _lock(db, user_id)
    note = db.scalar(select(SavedContext).where(
        SavedContext.id == note_id, SavedContext.user_id == user_id)
        .execution_options(populate_existing=True))
    if note is None:
        return False
    if memory.available(db):
        # Canonical text and every search chunk disappear in one transaction.
        # A failed delete raises; the conversation must not claim success.
        db.execute(text("DELETE FROM memory_chunks WHERE user_id=:u AND kind='chat' AND ref_id=:r"),
                   {"u": user_id, "r": note.id})
    # The context builder also reads recent events. Remove the matching
    # command excerpt so it cannot teach the forgotten detail again.
    db.execute(delete(Event).where(Event.user_id == user_id, Event.type == "voice_command",
               Event.payload["heard"].as_string() == note.text[:200]))
    db.delete(note)
    db.flush()
    return True


def resolve_forget(db, user_id: str, words: str, previous_user_words: list[str]) -> str:
    term = words[6:].strip(" :.!?").lower()
    query = select(SavedContext.id).where(SavedContext.user_id == user_id)
    if term in ("that", "this", "it"):
        if not previous_user_words:
            return ""
        query = query.where(SavedContext.text == previous_user_words[-1].strip())
    elif term:
        query = query.where(func.lower(SavedContext.text).contains(term, autoescape=True))
    else:
        return ""
    ids = list(db.scalars(query.limit(2)))
    return ids[0] if len(ids) == 1 else ""


def index_saved_context(limit: int = 20) -> int:
    from .db import SessionLocal
    with SessionLocal() as db:
        ids = list(db.execute(select(SavedContext.id, SavedContext.user_id)
                    .where(SavedContext.indexed.is_(False)).order_by(SavedContext.created_at).limit(limit)))
        notes = []
        for nid, uid in sorted(ids, key=lambda row: row[1]):
            _lock(db, uid)
            note = db.get(SavedContext, nid, populate_existing=True)
            if note is not None:
                _index(db, note)
                notes.append(note)
        db.commit()
        return sum(bool(n.indexed) for n in notes)
