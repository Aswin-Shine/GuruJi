"""Tests run against the real Postgres from docker compose (start `db` first).
OpenAI is always mocked — no test spends money."""
import os
import sys
import uuid
from pathlib import Path

# test_curriculum.py imports the chunking functions from scripts/ingest_curriculum.py
# directly. Chunking is where retrieval quality is decided, and re-implementing it
# inside the app package purely so tests could reach it would create a second copy
# free to drift from the one that actually builds the corpus. scripts/ is a plain
# directory, not a package, so it needs putting on the path explicitly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

# Before anything imports app.config, which reads the environment once. Under
# `docker compose run` the container carries the real .env, and without this a
# test that forgot a mock would call real OpenAI, or send a REAL WhatsApp message.
os.environ.update({
    "OPENAI_API_KEY": "sk-test-not-a-real-key",
    "PHOTO_QUESTIONS_ENABLED": "0",
    "WHATSAPP_ACCESS_TOKEN": "",
    "WHATSAPP_PHONE_NUMBER_ID": "",
    "WHATSAPP_GRAPH_API_VERSION": "",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient

from app.db.session import SessionLocal, init_db
from app.main import app
from app.modules.identity import service as identity
from app.modules.student_profile import service as profile


@pytest.fixture(scope="session", autouse=True)
def _schema():
    init_db(retries=3)


@pytest.fixture(autouse=True)
def _no_typing_indicator(monkeypatch):
    """The read/typing call is a second httpx.post per turn; tests that count sends
    would all have to discount it. Its own test calls the real function directly."""
    from app.modules.whatsapp import service as whatsapp
    monkeypatch.setattr(whatsapp, "mark_read_typing", lambda message_id: None)


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture()
def client():
    return TestClient(app)


def make_student(db, grade: int = 8):
    phone = f"+91{uuid.uuid4().int % 10**10:010d}"
    user = identity.get_or_create_user(db, phone, "student")
    student = profile.create_student(db, user.id, grade, "NCERT", "hinglish")
    return user, student, identity.issue_token(user.id)


def make_parent(db):
    phone = f"+91{uuid.uuid4().int % 10**10:010d}"
    user = identity.get_or_create_user(db, phone, "parent")
    return user, identity.issue_token(user.id)
