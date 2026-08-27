"""The identity interview endpoints. Voice: Nano's questions are served as mp3
(ElevenLabs, cached); the user's answers arrive as text (on-device speech
recognition client-side)."""
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import interview
from ..auth import current_user_id
from ..db import get_db
from ..models import InterviewSession, InterviewTurn
from ..voice import tts

router = APIRouter(prefix="/v1/interview", tags=["interview"])


def _turn_payload(db: Session, session: InterviewSession, text: str,
                  done: bool = False, progress: float = 0.0) -> dict:
    turn = db.scalar(
        select(InterviewTurn).where(InterviewTurn.session_id == session.id,
                                    InterviewTurn.role == "nano")
        .order_by(InterviewTurn.idx.desc()))
    return {
        "session_id": session.id,
        "question": text,
        "audio_url": f"/v1/interview/turns/{turn.id}/audio" if turn else None,
        "done": done,
        "progress": progress,
        "section": session.section,
    }


@router.post("/start")
def start_interview(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    session, question = interview.start(db, user_id)
    db.commit()
    return _turn_payload(db, session, question)


class Answer(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


@router.post("/{session_id}/answer")
def answer_interview(session_id: str, body: Answer,
                     user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    session = db.get(InterviewSession, session_id)
    if session is None or session.user_id != user_id:
        raise HTTPException(status_code=404, detail="No such interview")
    if session.status != "active":
        raise HTTPException(status_code=409, detail="Interview already completed")
    question, done, progress = interview.answer(db, session, body.text)
    db.commit()
    return _turn_payload(db, session, question, done, progress)


@router.get("/turns/{turn_id}/audio")
def turn_audio(turn_id: str, user_id: str = Depends(current_user_id),
               db: Session = Depends(get_db)):
    turn = db.get(InterviewTurn, turn_id)
    if turn is None:
        raise HTTPException(status_code=404, detail="No such turn")
    session = db.get(InterviewSession, turn.session_id)
    if session is None or session.user_id != user_id or turn.role != "nano":
        raise HTTPException(status_code=404, detail="No such turn")
    audio = tts(turn.text)
    if not audio:  # stub mode: 204 tells the client to skip playback
        return Response(status_code=204)
    return Response(content=audio, media_type="audio/mpeg")
