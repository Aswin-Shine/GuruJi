import json
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import CHEAP_MODEL
from app.db.session import SessionLocal
from app.modules.ai_orchestrator import llm
from app.modules.ai_orchestrator.prompts.memory_summarization import build_memory_prompt
from app.modules.memory.models import StudentMemory

log = logging.getLogger("guruji.memory")


def get_summary(db: Session, student_id: uuid.UUID) -> dict:
    mem = db.execute(select(StudentMemory).where(StudentMemory.student_id == student_id)).scalar_one_or_none()
    return dict(mem.summary_jsonb) if mem else {}


def set_summary(db: Session, student_id: uuid.UUID, summary: dict) -> None:
    mem = db.execute(select(StudentMemory).where(StudentMemory.student_id == student_id)).scalar_one_or_none()
    if mem is None:
        mem = StudentMemory(student_id=student_id, summary_jsonb=summary)
        db.add(mem)
    else:
        mem.summary_jsonb = summary
    db.commit()


def regenerate(student_id: uuid.UUID, grade: int, recent_messages: str) -> None:
    """Full rewrite via one cheap-model call. Failure is non-fatal — a stale
    memory summary must never block a tutoring reply.

    Opens its OWN session, because this runs as a background task after the HTTP
    response has returned, by which point get_db() has closed the request session.

    Gated by the same daily spend breaker as the tutoring call, since this is a real
    paid call. When the cap is tripped, regeneration is skipped SILENTLY: the breaker
    already alerts loudly on the tutoring path, and yesterday's summary simply
    persists one more session."""
    db = SessionLocal()
    try:
        try:
            llm.check_spend_cap(db)
        except llm.SpendCapExceeded:
            log.warning("memory regeneration skipped: daily spend cap tripped student_id=%s", student_id)
            return
        _regenerate_with_session(db, student_id, grade, recent_messages)
    finally:
        db.close()


def _regenerate_with_session(db: Session, student_id: uuid.UUID, grade: int, recent_messages: str) -> None:
    try:
        prompt = build_memory_prompt(grade, json.dumps(get_summary(db, student_id)), recent_messages)
        # No retry: this runs in the background and its failure is already non-fatal,
        # so paying 21s of worst-case tail for a nicety is not a trade.
        out, p_tok, c_tok = llm.chat(CHEAP_MODEL, "Return only valid JSON.", prompt, max_tokens=300, retry=False)
        llm.record_spend(db, p_tok, c_tok)  # memory spend counts against the breaker too
        summary = json.loads(out.strip().removeprefix("```json").removesuffix("```").strip())
        if isinstance(summary, dict):
            set_summary(db, student_id, summary)
    except Exception as exc:
        log.warning("memory regeneration skipped: %s", exc)
