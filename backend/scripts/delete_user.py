"""Manual admin script: delete a user's data across all tables, given a user_id.
Children's data — this must exist even before anyone asks (DPDP readiness).

Usage: python scripts/delete_user.py <user_id>
"""
import sys

from sqlalchemy import text

# Puts backend/ on sys.path; must come before any `app.*` import.
import _bootstrap  # noqa: F401  isort:skip

from app.db.session import SessionLocal


def delete_user(user_id: str) -> None:
    db = SessionLocal()
    try:
        student_row = db.execute(text("SELECT id FROM students WHERE user_id = :u"), {"u": user_id}).first()
        if student_row:
            sid = str(student_row[0])
            db.execute(text(
                "DELETE FROM messages WHERE conversation_id IN "
                "(SELECT id FROM conversations WHERE student_id = :s)"), {"s": sid})
            db.execute(text("DELETE FROM conversations WHERE student_id = :s"), {"s": sid})
            db.execute(text("DELETE FROM student_memory WHERE student_id = :s"), {"s": sid})
            db.execute(text("DELETE FROM moderation_flags WHERE student_id = :s"), {"s": sid})
            db.execute(text("DELETE FROM parent_links WHERE student_id = :s"), {"s": sid})
            db.execute(text("DELETE FROM students WHERE id = :s"), {"s": sid})
        db.execute(text("DELETE FROM parent_links WHERE parent_user_id = :u"), {"u": user_id})
        db.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
        db.commit()
        print(f"Deleted all data for user {user_id}.")
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python scripts/delete_user.py <user_id>")
    confirm = input(f"PERMANENTLY delete all data for user {sys.argv[1]}? Type 'yes': ")
    if confirm.strip().lower() != "yes":
        sys.exit("Aborted.")
    delete_user(sys.argv[1])
