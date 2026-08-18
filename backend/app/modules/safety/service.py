"""Persistence for exchanges the moderation API flagged, in either direction."""
import logging
import uuid

from sqlalchemy.orm import Session

from app.modules.safety.models import ModerationFlag

log = logging.getLogger("guruji.safety")

MAX_STORED_CHARS = 2000


def record_flag(db: Session, student_id: uuid.UUID, direction: str, content: str) -> None:
    """Never raises. A failure to record a flag must not turn into a failure to give
    the student a safe response — the safe response is the thing that matters in the
    moment; the record is for the parent afterwards."""
    try:
        db.add(ModerationFlag(student_id=student_id, direction=direction, content=content[:MAX_STORED_CHARS]))
        db.commit()
    except Exception as exc:
        log.error("failed to record moderation flag student_id=%s direction=%s: %s", student_id, direction, exc)
        db.rollback()


def list_flags(db: Session, student_id: uuid.UUID, limit: int = 50) -> list[ModerationFlag]:
    """Read path for the parent view. No endpoint exposes this yet — parent auth is
    blocked on real OTP delivery — so this exists to be called by an admin script or
    the parent summary route the moment that lands."""
    return list(
        db.query(ModerationFlag)
        .filter(ModerationFlag.student_id == student_id)
        .order_by(ModerationFlag.flagged_at.desc())
        .limit(limit)
    )
