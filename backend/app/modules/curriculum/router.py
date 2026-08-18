"""Curriculum read API.

Exists so the client never has to hardcode what the corpus contains. A hardcoded
copy of curriculum knowledge rots the moment a class is ingested or re-ingested
under a different subject label.

Both routes derive from `curriculum_documents` joined to `curriculum_chunks`, so
a subject with no embedded chunks never appears. A menu that offers Maths for a
class whose Maths has not been ingested promises a book GuruJi cannot open —
which is the same failure as the original "Coal kaise banta hai?" opener, where
the very first tap taught the child that GuruJi does not know things.

Public to any signed-in user: this is a list of textbook names, not student data.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.curriculum import service as curriculum
from app.modules.identity.dependencies import CurrentUser, get_current_user

router = APIRouter(prefix="/v1/curriculum", tags=["curriculum"])


@router.get("/subjects")
def list_subjects(
    _current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Every (grade, subject) pair with at least one embedded chunk.

    The UI uses this to decide whether a class needs a subject picker at all.
    Today every class has exactly one subject, so the correct rendering is no
    picker — showing a menu with a single option is noise pretending to be a
    choice. The day a second subject lands for a class, the picker appears with
    no frontend change.
    """
    return curriculum.subjects_by_grade(db)


@router.get("/chapters")
def list_chapters(
    grade: int,
    subject: str | None = None,
    _current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Chapters available for a class, optionally narrowed to one subject.

    This is what makes a "what's in your book" screen possible. Worth building
    on: the product's single most common measured failure is a question the
    corpus does not cover, and a student currently discovers that only by being
    refused. Showing the chapter list turns a rejection into a boundary they
    understood beforehand.
    """
    rows = db.execute(
        text(
            "SELECT d.subject, d.chapter_no, d.title, count(c.id) AS chunks "
            "FROM curriculum_documents d "
            "JOIN curriculum_chunks c ON c.document_id = d.id "
            "WHERE d.grade = :g AND (:s IS NULL OR d.subject = :s) "
            "GROUP BY d.subject, d.chapter_no, d.title "
            "ORDER BY d.subject, d.chapter_no"
        ),
        {"g": grade, "s": subject},
    ).all()
    return [
        {"subject": r[0], "chapter_no": r[1], "title": r[2], "chunks": r[3]}
        for r in rows
    ]
