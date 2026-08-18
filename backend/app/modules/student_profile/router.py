import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.identity import service as identity
from app.modules.identity.dependencies import CurrentUser, get_current_user
from app.modules.student_profile import service
from app.modules.safety import service as safety
from app.modules.student_profile.schemas import (
    FlaggedExchangeOut,
    LinkParentRequest,
    LinkParentResponse,
    StudentCreate,
    StudentOut,
    StudentPatch,
    StudentSummaryOut,
    VerifyLinkRequest,
)

router = APIRouter(prefix="/v1/students", tags=["students"])


def _owned_student_or_403(db: Session, current: CurrentUser, student_id: uuid.UUID):
    """Role AND ownership check — role alone would be an IDOR hole."""
    student = service.get_student(db, student_id)
    if student is None:
        raise HTTPException(status_code=404, detail="Student not found")
    if current.role == "student" and current.student_id == student_id:
        return student
    raise HTTPException(status_code=403, detail="Not your profile")


@router.get("/me", response_model=StudentOut)
def my_profile(
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StudentOut:
    """Resolve the caller's own student row from the token.

    MUST be declared before /{student_id}: FastAPI matches in declaration order, so
    the parameterised route would otherwise swallow "me" and 422 on the UUID parse.
    """
    if current.role != "student":
        raise HTTPException(status_code=403, detail="Students only")
    student = service.get_student_by_user(db, current.user_id)
    if student is None:
        raise HTTPException(status_code=404, detail="No student profile yet")
    return student


@router.post("", response_model=StudentOut, status_code=201)
def create_profile(body: StudentCreate, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> StudentOut:
    if current.role != "student":
        raise HTTPException(status_code=403, detail="Only students create a profile")
    if current.student_id is not None:
        raise HTTPException(status_code=409, detail="Profile already exists")
    return service.create_student(db, current.user_id, body.grade, body.board, body.preferred_language)


@router.get("/{student_id}", response_model=StudentOut)
def get_profile(student_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> StudentOut:
    return _owned_student_or_403(db, current, student_id)


@router.patch("/{student_id}", response_model=StudentOut)
def patch_profile(student_id: uuid.UUID, body: StudentPatch, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> StudentOut:
    student = _owned_student_or_403(db, current, student_id)
    if body.grade is not None:
        student.grade = body.grade
    # An empty string clears rather than storing "" — a blank name should fall back
    # to the neutral default, not render an empty chat header.
    if body.display_name is not None:
        student.display_name = body.display_name.strip() or None
    if body.avatar is not None:
        student.avatar = body.avatar.strip() or None
    if body.preferred_language is not None:
        student.preferred_language = body.preferred_language.strip() or "hinglish"
    db.commit()
    return student


@router.post("/{student_id}/link-parent", response_model=LinkParentResponse, status_code=201)
def link_parent(
    student_id: uuid.UUID,
    body: LinkParentRequest,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LinkParentResponse:
    """Student-initiated. Creates an UNVERIFIED link and returns a PIN the student
    relays to the parent (no SMS in Phase 1). Parent activates it via /verify-parent-link."""
    student = _owned_student_or_403(db, current, student_id)  # role + ownership
    parent_user = identity.get_or_create_user(db, body.parent_phone_number, "parent")
    if parent_user.role != "parent":
        raise HTTPException(status_code=409, detail="That phone number belongs to a non-parent account")
    link, pin = service.create_parent_link(db, student, parent_user.id)
    return LinkParentResponse(parent_user_id=parent_user.id, link_pin=pin, verified=link.verified_at is not None)


@router.post("/{student_id}/verify-parent-link", status_code=204)
def verify_parent_link(
    student_id: uuid.UUID,
    body: VerifyLinkRequest,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Parent-submitted PIN activates the link (sets verified_at)."""
    if current.role != "parent":
        raise HTTPException(status_code=403, detail="Parents only")
    service.verify_parent_link(db, current.user_id, student_id, body.link_pin)


@router.get("/{student_id}/summary", response_model=StudentSummaryOut)
def parent_summary(student_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    if current.role != "parent":
        raise HTTPException(status_code=403, detail="Parents only")
    if not service.parent_is_linked(db, current.user_id, student_id):
        raise HTTPException(status_code=403, detail="No verified link to this student")
    student = service.get_student(db, student_id)
    if student is None:
        raise HTTPException(status_code=404, detail="Student not found")
    return service.summary_for_parent(db, student)


@router.get("/{student_id}/flagged", response_model=list[FlaggedExchangeOut])
def parent_flagged_exchanges(
    student_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list:
    """Flagged exchanges, for a verified parent.

    Two-gate authorization, same as /summary: role AND a VERIFIED parent_links row.
    Role alone would let any parent read any child's flagged exchanges by changing the
    UUID — the worst version of that bug available here, given what these rows contain.

    This is the ONE place a parent sees verbatim child-written text. /summary stays
    transcript-free by design; the exception exists because a paraphrase would be
    useless for the judgement the parent has to make.
    """
    if current.role != "parent":
        raise HTTPException(status_code=403, detail="Parents only")
    if not service.parent_is_linked(db, current.user_id, student_id):
        raise HTTPException(status_code=403, detail="No verified link to this student")
    return safety.list_flags(db, student_id)
