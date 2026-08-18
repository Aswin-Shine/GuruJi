"""Fixes #21-#26: output moderation, flag persistence, phone allow-list, latency
deadline, dead-schema removal, real health check."""
import uuid as _uuid
from unittest.mock import patch

import pytest
from sqlalchemy import text as _t

from app.modules.ai_orchestrator import orchestrator
from app.modules.ai_orchestrator.orchestrator import QueryPlan, TurnResult
from app.modules.safety import service as safety
from tests.conftest import make_student


def _turn(reply="ok", tokens=5, model="test-model", grounding="grounded", citation=None):
    """orchestrate() returns a TurnResult, not a bare tuple. Tests that mock
    it must return the same shape or they assert against a fiction — the real thing
    now carries provenance the route reads. TurnResult stays iterable as
    (reply, tokens, model_used), so the assertions below are unchanged."""
    return TurnResult(reply, tokens, model, grounding, citation)


@pytest.fixture(autouse=True)
def _deterministic_planner():
    """plan_query() makes its own paid llm.chat call. Several tests in this
    file assert on llm.chat call_count to prove a regeneration did or did not happen —
    an unmocked planner silently eats one of those calls and the assertion then
    measures the wrong thing. Pinned as an autouse fixture rather than threaded
    through _mocks() so every existing `with ms[0], ms[1], ...` block stays untouched."""
    with patch.object(orchestrator, "plan_query", return_value=QueryPlan(True, "test query")):
        yield

SAFE_REPLY = "Yeh toh easy hai! Photosynthesis mein plant sunlight use karta hai. Kaunsa part involved hai?"


def _mocks(chat_return, moderate_side_effect):
    """Standard orchestrate mock set: moderation behaviour is the variable."""
    return (
        patch.object(orchestrator.llm, "moderate", side_effect=moderate_side_effect),
        patch.object(orchestrator.llm, "check_spend_cap"),
        patch.object(orchestrator.llm, "record_spend"),
        patch.object(orchestrator.llm, "embed", return_value=[0.0] * 1536),
        patch.object(orchestrator.curriculum, "retrieve",
                     return_value=[orchestrator.curriculum.Chunk(1, "chunk", 0.9)]),
        patch.object(orchestrator.llm, "chat", return_value=chat_return),
    )


def test_output_moderation_blocks_unsafe_reply(db):
    """input-only moderation left a four-phrase English blocklist as the
    entire guard on what the model says to a child in Hinglish. A flagged OUTPUT must
    never reach the student, and must not be retried into existence."""
    _, student, _ = make_student(db)
    # moderate(): False on the inbound message, True on the generated reply.
    ms = _mocks(("something the moderation endpoint flags", 100, 50), [False, True])
    with ms[0] as mock_mod, ms[1], ms[2], ms[3], ms[4], ms[5] as mock_chat:
        reply, _, _ = orchestrator.orchestrate(db, student.id, 8, "NCERT", "explain this", "")
    assert reply == orchestrator.FALLBACK_UNSAFE
    assert mock_mod.call_count == 2          # input AND output
    assert mock_chat.call_count == 1         # no regeneration roll of the dice


def test_flagged_input_is_persisted_not_just_logged(db):
    """the Founder Workbook promises parents can review what their child
    tried to ask. A log line is not that."""
    _, student, _ = make_student(db)
    with patch.object(orchestrator.llm, "moderate", return_value=True):
        reply, _, tag = orchestrator.orchestrate(db, student.id, 8, "NCERT", "bad thing", "")
    assert tag == "moderation_blocked" and reply == orchestrator.FALLBACK_MODERATED
    rows = db.execute(
        _t("SELECT direction, content FROM moderation_flags WHERE student_id = :s"),
        {"s": str(student.id)},
    ).all()
    assert [(r[0], r[1]) for r in rows] == [("inbound", "bad thing")]


def test_flagged_output_is_persisted(db):
    _, student, _ = make_student(db)
    ms = _mocks(("flagged reply text", 100, 50), [False, True])
    with ms[0], ms[1], ms[2], ms[3], ms[4], ms[5]:
        orchestrator.orchestrate(db, student.id, 8, "NCERT", "explain this", "")
    directions = [r[0] for r in db.execute(
        _t("SELECT direction FROM moderation_flags WHERE student_id = :s"), {"s": str(student.id)}).all()]
    assert directions == ["outbound"]


def test_record_flag_failure_never_breaks_the_reply(db):
    """A failure to record a flag must not become a failure to give the student a safe
    response — the safe response is what matters in the moment."""
    _, student, _ = make_student(db)
    with patch.object(safety, "ModerationFlag", side_effect=RuntimeError("boom")):
        safety.record_flag(db, student.id, "inbound", "x")  # must not raise


def test_blocklist_covers_transliterated_terms():
    """the tripwire list was English-only on a Hinglish product. It is still
    only a tripwire — moderation is the real guard — but it should at least not be
    trivially sidestepped by transliteration."""
    assert not orchestrator._validate("Bas khudkushi kar lo", no_context=False)
    assert orchestrator._validate("Chalo fractions practice karte hain!", no_context=False)


def test_regeneration_skipped_past_latency_deadline(db):
    """a validation failure used to buy a second full model call — up to ~21s
    more on a request the student is already waiting on. Past the deadline, they get a
    safe answer now instead of a better one much later."""
    _, student, _ = make_student(db)
    bad = "Quantum entanglement links two particles."  # no uncertainty marker
    ms = _mocks((bad, 100, 50), [False, False])
    with ms[0], ms[1], ms[2], ms[3], \
         patch.object(orchestrator.curriculum, "retrieve", return_value=[]), \
         ms[5] as mock_chat, \
         patch.object(orchestrator, "REGEN_DEADLINE_S", -1.0):  # deadline already blown
        reply, _, _ = orchestrator.orchestrate(db, student.id, 8, "NCERT", "quantum kya hai", "")
    assert reply == orchestrator.FALLBACK_UNSAFE
    assert mock_chat.call_count == 1  # regeneration skipped


def test_regeneration_still_happens_within_deadline(db):
    """Regression guard: the deadline must not disable the documented one-retry path."""
    _, student, _ = make_student(db)
    bad = "Quantum entanglement links two particles."
    good = "Yeh abhi aapki textbook mein nahi hai, dost. School topic poochho!"
    with patch.object(orchestrator.llm, "moderate", return_value=False), \
         patch.object(orchestrator.llm, "check_spend_cap"), \
         patch.object(orchestrator.llm, "record_spend"), \
         patch.object(orchestrator.llm, "embed", return_value=[0.0] * 1536), \
         patch.object(orchestrator.curriculum, "retrieve", return_value=[]), \
         patch.object(orchestrator.llm, "chat", side_effect=[(bad, 100, 50), (good, 100, 50)]) as mock_chat:
        reply, _, _ = orchestrator.orchestrate(db, student.id, 8, "NCERT", "quantum kya hai", "")
    assert "textbook" in reply.lower() and mock_chat.call_count == 2


def test_memory_regeneration_does_not_retry():
    """a background nicety must not cost 21s of worst-case tail."""
    import inspect
    from app.modules.memory import service as memory_service
    src = inspect.getsource(memory_service._regenerate_with_session)
    assert "retry=False" in src


def test_allowlisted_number_passes_and_unknown_number_is_dropped(client, db):
    """get_or_create_user() provisions an account on first contact, so an
    unknown number must be rejected BEFORE it, or anyone who learns the number gets
    billed tutoring."""
    from app.modules.conversation import router as conv_router
    from tests.test_conversation import _msg_payload, _post_webhook

    _, student, _ = make_student(db)
    phone = db.execute(_t("SELECT phone_number FROM users WHERE id = "
                          "(SELECT user_id FROM students WHERE id = :s)"), {"s": str(student.id)}).scalar_one()
    stranger = "+919000000001"

    with patch.object(conv_router, "ALLOWED_PHONE_NUMBERS", [phone]), \
         patch("app.modules.conversation.service.orchestrate", return_value=_turn("ok", 5, "m")):
        allowed = _post_webhook(client, _msg_payload(phone, "gravity samjhao", f"wamid.{_uuid.uuid4()}"))
        blocked = _post_webhook(client, _msg_payload(stranger, "gravity samjhao", f"wamid.{_uuid.uuid4()}"))

    assert allowed.json()["status"] == "ok"
    assert blocked.status_code == 200 and blocked.json()["status"] == "not_allowed"
    # and no account was created for the stranger
    assert db.execute(_t("SELECT count(*) FROM users WHERE phone_number = :p"),
                      {"p": stranger}).scalar_one() == 0


def test_empty_allowlist_means_open(client, db):
    """Default stays open so local dev and an intentionally open pilot both work —
    the boot warning is what makes that a choice rather than an accident."""
    from app.modules.conversation import router as conv_router
    from tests.test_conversation import _msg_payload, _post_webhook

    with patch.object(conv_router, "ALLOWED_PHONE_NUMBERS", []), \
         patch("app.modules.conversation.service.orchestrate", return_value=_turn("ok", 5, "m")):
        resp = _post_webhook(client, _msg_payload("+919000000002", "7", f"wamid.{_uuid.uuid4()}"))
    assert resp.json()["status"] in {"ok", "onboarded", "onboarding"}


def test_health_reports_503_when_database_is_down(client):
    """/health returned a hardcoded 'ok' while the architecture doc claimed a
    503 on database failure — so an orchestrator would keep a dead app in service."""
    from app.db import session as db_session

    class _DeadSession:
        def execute(self, *a, **k):
            raise RuntimeError("connection refused")

        def close(self):
            pass

    assert client.get("/health").status_code == 200
    with patch.object(db_session, "SessionLocal", lambda: _DeadSession()):
        assert client.get("/health").status_code == 503


def test_dead_confidence_score_column_is_gone(db):
    """never written by any code path — always the 0.5 default. Dead schema
    is a lie about intent."""
    from app.modules.memory.models import StudentMemory

    assert not hasattr(StudentMemory, "confidence_score")
    cols = [r[0] for r in db.execute(_t(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'student_memory'")).all()]
    assert "confidence_score" not in cols
