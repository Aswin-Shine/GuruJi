import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.identity import service


@dataclass
class CurrentUser:
    user_id: uuid.UUID
    role: str
    student_id: uuid.UUID | None  # set when role == student


def get_current_user(request: Request, db: Session = Depends(get_db)) -> CurrentUser:
    # DELIBERATE: auth is header-based Bearer only, never cookies. Because no cookie
    # carries auth, no CSRF-token machinery is needed. Do NOT "fix" this into a
    # cookie session without adding CSRF protection — that change is not free.
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    user_id = service.verify_token(auth.removeprefix("Bearer "), db)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid, expired, or revoked token")
    user = service.get_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Unknown user")
    student_id = None
    if user.role == "student":
        row = db.execute(text("SELECT id FROM students WHERE user_id = :u"), {"u": str(user.id)}).first()
        student_id = row[0] if row else None
    return CurrentUser(user_id=user.id, role=user.role, student_id=student_id)


def require_admin(current: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if current.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return current
