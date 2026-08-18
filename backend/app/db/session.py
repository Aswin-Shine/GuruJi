import time
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import DATABASE_URL

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

SCHEMA_SQL = Path(__file__).with_name("schema.sql").read_text()


def init_db(retries: int = 30) -> None:
    """Apply idempotent schema on startup. Retries while Postgres container boots."""
    for attempt in range(retries):
        try:
            with engine.begin() as conn:
                conn.execute(text(SCHEMA_SQL))
            return
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(1)


def get_db():
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
