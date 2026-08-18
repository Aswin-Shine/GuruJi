import hashlib
import hmac
import uuid

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import SECRET_KEY
from app.modules.student_profile.models import ParentLink, Student


def get_student(db: Session, student_id: uuid.UUID) -> Student | None:
    return db.get(Student, student_id)


def get_student_by_user(db: Session, user_id: uuid.UUID) -> Student | None:
    return db.execute(select(Student).where(Student.user_id == user_id)).scalar_one_or_none()


def create_student(db: Session, user_id: uuid.UUID, grade: int, board: str, preferred_language: str) -> Student:
    student = Student(user_id=user_id, grade=grade, board=board, preferred_language=preferred_language)
    db.add(student)
    db.commit()
    return student


def parent_is_linked(db: Session, parent_user_id: uuid.UUID, student_id: uuid.UUID) -> bool:
    """Verified link required — an unverified row grants nothing."""
    link = db.execute(
        select(ParentLink).where(
            ParentLink.parent_user_id == parent_user_id,
            ParentLink.student_id == student_id,
            ParentLink.verified_at.is_not(None),
        )
    ).scalar_one_or_none()
    return link is not None


def _link_pin(link_id: uuid.UUID) -> str:
    """6-digit PIN derived from the link row id + SECRET_KEY. Deterministic, so it
    needs no storage column (the fixed schema has none). The student relays it to the
    parent out-of-band; the parent submits it to verify. Not real SMS — that's Phase 2.
    ponytail: HMAC-derived PIN over a stored PIN column; add a real one-time code
    table only when real OTP delivery lands in Phase 2."""
    digest = hmac.new(SECRET_KEY.encode(), str(link_id).encode(), hashlib.sha256).hexdigest()
    return f"{int(digest, 16) % 1_000_000:06d}"


def create_parent_link(db: Session, student: Student, parent_user_id: uuid.UUID) -> tuple[ParentLink, str]:
    """Student-initiated: create an UNVERIFIED link to a parent user and return its PIN.
    Idempotent — re-inviting the same parent returns the existing row + its PIN."""
    existing = db.execute(
        select(ParentLink).where(
            ParentLink.parent_user_id == parent_user_id,
            ParentLink.student_id == student.id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, _link_pin(existing.id)
    link = ParentLink(parent_user_id=parent_user_id, student_id=student.id)
    db.add(link)
    db.commit()
    return link, _link_pin(link.id)


# Brute-force guard on the 6-digit PIN space. In-process counter, so a restart resets
# it — worst case an attacker gets 5 more tries per restart.
#
# The PIN is HMAC-derived from link.id and never expires, so lockout must invalidate
# the PIN itself: DELETE the link row. A new invite means a new UUID, therefore a new
# PIN, with no stored attempt column needed.
MAX_PIN_ATTEMPTS = 5
_pin_attempts: dict[uuid.UUID, int] = {}


def verify_parent_link(db: Session, parent_user_id: uuid.UUID, student_id: uuid.UUID, pin: str) -> None:
    """Parent-submitted PIN sets verified_at. Wrong PIN or no pending link -> 403/404.
    The 5th wrong PIN deletes the link, so the student must re-invite."""
    link = db.execute(
        select(ParentLink).where(
            ParentLink.parent_user_id == parent_user_id,
            ParentLink.student_id == student_id,
        )
    ).scalar_one_or_none()
    if link is None:
        raise HTTPException(status_code=404, detail="No pending link for this parent and student")
    if not hmac.compare_digest(pin, _link_pin(link.id)):
        attempts = _pin_attempts.get(link.id, 0) + 1
        if attempts >= MAX_PIN_ATTEMPTS:
            db.delete(link)
            db.commit()
            _pin_attempts.pop(link.id, None)
            raise HTTPException(
                status_code=403,
                detail="Too many wrong PINs — link cancelled. Ask the student to re-invite you (new PIN).",
            )
        _pin_attempts[link.id] = attempts
        raise HTTPException(status_code=403, detail="Incorrect link PIN")
    _pin_attempts.pop(link.id, None)
    if link.verified_at is None:
        db.execute(text("UPDATE parent_links SET verified_at = now() WHERE id = :i"), {"i": str(link.id)})
        db.commit()


def summary_for_parent(db: Session, student: Student) -> dict:
    total = db.execute(
        text(
            "SELECT count(*) FROM messages m JOIN conversations c ON c.id = m.conversation_id "
            "WHERE c.student_id = :s AND m.sender = 'student'"
        ),
        {"s": str(student.id)},
    ).scalar_one()
    mem = db.execute(
        text("SELECT summary_jsonb FROM student_memory WHERE student_id = :s"), {"s": str(student.id)}
    ).scalar_one_or_none() or {}
    return {
        "student_id": student.id,
        "grade": student.grade,
        "board": student.board,
        "total_messages": total,
        "struggle_topics": mem.get("struggle_topics", []),
        "mastered_topics": mem.get("mastered_topics", []),
    }
