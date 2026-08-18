import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.config import WHATSAPP_APP_SECRET
from app.modules.ai_orchestrator.orchestrator import TurnResult
from app.modules.conversation import service
from tests.conftest import make_student


def _turn(reply="ok", tokens=5, model="test-model", grounding="grounded", citation=None):
    """orchestrate() returns a TurnResult, not a bare tuple. Tests that mock
    it must return the same shape or they assert against a fiction — the real thing
    now carries provenance the route reads. TurnResult stays iterable as
    (reply, tokens, model_used), so the assertions below are unchanged."""
    return TurnResult(reply, tokens, model, grounding, citation)


def _post_webhook(client, payload: dict):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(WHATSAPP_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/v1/webhooks/whatsapp",
        content=body,
        headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
    )


def _msg_payload(phone: str, text: str, msg_id: str | None = "wamid.TEST"):
    msg = {"from": phone, "type": "text", "text": {"body": text}}
    if msg_id is not None:
        msg["id"] = msg_id
    return {"entry": [{"changes": [{"value": {"messages": [msg]}}]}]}


def test_webhook_rejects_bad_signature(client):
    body = json.dumps({"entry": []}).encode()
    resp = client.post(
        "/v1/webhooks/whatsapp",
        content=body,
        headers={"X-Hub-Signature-256": "sha256=deadbeef", "Content-Type": "application/json"},
    )
    assert resp.status_code == 403


def test_webhook_accepts_valid_signature(client):
    body = json.dumps({"entry": []}).encode()
    sig = "sha256=" + hmac.new(WHATSAPP_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    resp = client.post(
        "/v1/webhooks/whatsapp",
        content=body,
        headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


def test_four_hour_conversation_boundary(db):
    _, student, _ = make_student(db)
    conv1, is_new1 = service.get_or_create_conversation(db, student.id, "whatsapp")
    assert is_new1
    conv1.last_message_at = datetime.now(timezone.utc)
    db.commit()
    conv2, is_new2 = service.get_or_create_conversation(db, student.id, "whatsapp")
    assert conv2.id == conv1.id and not is_new2
    conv1.last_message_at = datetime.now(timezone.utc) - timedelta(hours=5)
    db.commit()
    conv3, is_new3 = service.get_or_create_conversation(db, student.id, "whatsapp")
    assert conv3.id != conv1.id and is_new3


def test_student_cannot_read_others_conversation(client, db):
    _, student_a, token_a = make_student(db)
    _, student_b, _ = make_student(db)
    conv_b, _ = service.get_or_create_conversation(db, student_b.id, "whatsapp")
    resp = client.get(f"/v1/conversations/{conv_b.id}/messages", headers={"Authorization": f"Bearer {token_a}"})
    assert resp.status_code == 403


def test_webhook_handler_is_sync():
    """the webhook was `async def` calling only blocking I/O (sync
    SQLAlchemy, sync OpenAI), serializing ALL webhook traffic on the single event-loop
    thread. A plain `def` runs on Starlette's thread pool. This assert is the
    regression guard — flipping it back to `async def` fails here."""
    from app.modules.conversation.router import webhook_inbound
    assert not asyncio.iscoroutinefunction(webhook_inbound)


def test_web_send_uses_same_orchestration_and_returns_conversation_id(client, db):
    """B1: web channel calls the one handle_student_message (channel='web'); the
    returned conversation_id immediately works with the existing history endpoint."""
    _, student, token = make_student(db)
    hdr = {"Authorization": f"Bearer {token}"}
    with patch.object(service, "orchestrate", return_value=_turn("Bilkul sahi!", 10, "test-model")):
        resp = client.post("/v1/conversations/messages", json={"text": "5 x 7 kya hota hai?"}, headers=hdr)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["reply"] == "Bilkul sahi!"
    conv_id = data["conversation_id"]
    hist = client.get(f"/v1/conversations/{conv_id}/messages", headers=hdr)
    assert hist.status_code == 200
    senders = [m["sender"] for m in hist.json()]
    assert senders == ["student", "assistant"]
    from sqlalchemy import text as _t
    row = db.execute(_t("SELECT channel FROM conversations WHERE id = :i"), {"i": conv_id}).scalar_one()
    assert row == "web"


def test_web_send_requires_student_role(client, db):
    """IDOR/role guard: identity comes from the token only; a parent gets 403."""
    from tests.conftest import make_parent
    _, parent_token = make_parent(db)
    resp = client.post(
        "/v1/conversations/messages", json={"text": "hi"},
        headers={"Authorization": f"Bearer {parent_token}"},
    )
    assert resp.status_code == 403
    assert client.post("/v1/conversations/messages", json={"text": "hi"}).status_code == 401


def test_web_burst_hits_same_rate_limit_as_whatsapp(client, db):
    """rate-limit parity. A web burst past RATE_LIMIT_PER_MIN gets 429 —
    the same per-user in-process cap the webhook path enforces, not a separate or
    missing one."""
    from app.config import RATE_LIMIT_PER_MIN
    _, student, token = make_student(db)
    hdr = {"Authorization": f"Bearer {token}"}
    with patch.object(service, "orchestrate", return_value=_turn("ok", 1, "test-model")):
        codes = [
            client.post("/v1/conversations/messages", json={"text": "q"}, headers=hdr).status_code
            for _ in range(RATE_LIMIT_PER_MIN + 1)
        ]
    assert codes[:RATE_LIMIT_PER_MIN] == [200] * RATE_LIMIT_PER_MIN
    assert codes[-1] == 429


def test_list_conversations_scoped_and_paginated(client, db):
    """B2: caller sees ONLY their own conversations, newest activity first,
    limit/offset honored. No student_id parameter exists to tamper with."""
    _, student_a, token_a = make_student(db)
    _, student_b, _ = make_student(db)
    conv_old, _ = service.get_or_create_conversation(db, student_a.id, "whatsapp")
    conv_old.last_message_at = datetime.now(timezone.utc) - timedelta(hours=6)
    db.commit()
    conv_new, _ = service.get_or_create_conversation(db, student_a.id, "whatsapp")
    conv_new.last_message_at = datetime.now(timezone.utc)
    db.commit()
    service.get_or_create_conversation(db, student_b.id, "whatsapp")

    hdr = {"Authorization": f"Bearer {token_a}"}
    resp = client.get("/v1/conversations", headers=hdr)
    assert resp.status_code == 200
    ids = [c["id"] for c in resp.json()]
    assert ids == [str(conv_new.id), str(conv_old.id)]  # own only, newest first

    page2 = client.get("/v1/conversations?limit=1&offset=1", headers=hdr).json()
    assert [c["id"] for c in page2] == [str(conv_old.id)]


def test_duplicate_webhook_message_id_short_circuits(client, db):
    """Meta retries anything slower than its ack window. The same message id
    delivered twice must cost exactly one LLM call and leave exactly one student row."""
    from sqlalchemy import text as _t
    import uuid as _uuid
    _, student, _ = make_student(db)
    phone = db.execute(_t("SELECT phone_number FROM users WHERE id = ("
                          "SELECT user_id FROM students WHERE id = :s)"), {"s": str(student.id)}).scalar_one()
    msg_id = f"wamid.{_uuid.uuid4()}"
    payload = _msg_payload(phone, "photosynthesis kya hota hai?", msg_id)

    with patch.object(service, "orchestrate", return_value=_turn("Bilkul sahi!", 10, "test-model")) as mock_orch:
        first = _post_webhook(client, payload)
        second = _post_webhook(client, payload)

    assert first.status_code == 200 and first.json()["status"] == "ok"
    assert second.status_code == 200 and second.json()["status"] == "duplicate"
    assert mock_orch.call_count == 1  # second delivery never reached the model
    count = db.execute(_t(
        "SELECT count(*) FROM messages m JOIN conversations c ON c.id = m.conversation_id "
        "WHERE c.student_id = :s AND m.sender = 'student'"), {"s": str(student.id)}).scalar_one()
    assert count == 1


def test_webhook_missing_message_id_still_processes(client, db):
    """a payload with no message id is NOT rejected — dedupe is skipped and
    processing continues. Dropping real student questions over a missing field would
    be a worse bug than the duplicate this guards against."""
    from sqlalchemy import text as _t
    _, student, _ = make_student(db)
    phone = db.execute(_t("SELECT phone_number FROM users WHERE id = ("
                          "SELECT user_id FROM students WHERE id = :s)"), {"s": str(student.id)}).scalar_one()
    with patch.object(service, "orchestrate", return_value=_turn("ok", 5, "test-model")):
        resp = _post_webhook(client, _msg_payload(phone, "gravity samjhao", msg_id=None))
    assert resp.status_code == 200 and resp.json()["status"] == "ok"


def test_webhook_rejects_oversized_message(client, db):
    """webhook had no length cap while send_message() did."""
    from sqlalchemy import text as _t
    import uuid as _uuid
    _, student, _ = make_student(db)
    phone = db.execute(_t("SELECT phone_number FROM users WHERE id = ("
                          "SELECT user_id FROM students WHERE id = :s)"), {"s": str(student.id)}).scalar_one()
    with patch.object(service, "orchestrate") as mock_orch:
        resp = _post_webhook(client, _msg_payload(phone, "x" * 2001, f"wamid.{_uuid.uuid4()}"))
    assert resp.status_code == 200 and resp.json()["status"] == "rejected"
    mock_orch.assert_not_called()


def test_webhook_rejects_empty_message(client, db):
    from sqlalchemy import text as _t
    import uuid as _uuid
    _, student, _ = make_student(db)
    phone = db.execute(_t("SELECT phone_number FROM users WHERE id = ("
                          "SELECT user_id FROM students WHERE id = :s)"), {"s": str(student.id)}).scalar_one()
    with patch.object(service, "orchestrate") as mock_orch:
        resp = _post_webhook(client, _msg_payload(phone, "   \n  ", f"wamid.{_uuid.uuid4()}"))
    assert resp.status_code == 200 and resp.json()["status"] == "rejected"
    mock_orch.assert_not_called()


def test_window_widened_to_eight(db):
    """4 messages is shorter than one 3-step pedagogy ladder cycle.
    The window is 8, ordered oldest->newest, and char-capped."""
    from app.modules.conversation.models import Message
    _, student, _ = make_student(db)
    conv, _is_new = service.get_or_create_conversation(db, student.id, "web")
    for i in range(10):
        db.add(Message(conversation_id=conv.id, sender="student" if i % 2 == 0 else "assistant", content=f"m{i}"))
        db.commit()
    history = service._recent_transcript(db, conv.id, limit=service.HISTORY_TURNS,
                                         max_chars=service.HISTORY_MAX_CHARS)
    lines = history.splitlines()
    assert len(lines) == 8
    assert lines[0].endswith("m2") and lines[-1].endswith("m9")  # newest 8, oldest first


def test_recent_transcript_char_cap_bounds_prompt():
    """The message cap alone bounds nothing: 8 messages at the 2000-char inbound
    ceiling is ~4k tokens — the whole context budget spent on history."""
    assert service.HISTORY_MAX_CHARS <= 4000


def test_memory_regen_scheduled_not_inline(db):
    """handle_student_message must NOT call regenerate itself — it returns
    the args and the router schedules them after the response."""
    from app.modules.memory import service as memory_service
    _, student, _ = make_student(db)
    old, _ = service.get_or_create_conversation(db, student.id, "web")
    old.last_message_at = datetime.now(timezone.utc) - timedelta(hours=6)
    db.commit()

    with patch.object(memory_service, "regenerate") as mock_regen, \
         patch.object(service, "orchestrate", return_value=_turn("ok", 5, "test-model")):
        reply, conv_id, regen = service.handle_student_message(db, student, "web", "fractions samjhao")
    mock_regen.assert_not_called()          # not inline
    assert regen is not None                # but scheduled by the caller
    assert regen.student_id == student.id and regen.grade == student.grade
    assert conv_id != old.id


def test_memory_regen_uses_own_session(db):
    """regenerate() runs after the response, when get_db() has already closed
    the request session. It must open its own — proven by closing the caller's first."""
    import json as _json
    from app.db.session import SessionLocal
    from app.modules.memory import service as memory_service
    from sqlalchemy import text as _t

    _, student, _ = make_student(db)
    student_id = student.id
    db.close()  # simulate the request session being gone

    with patch.object(memory_service.llm, "check_spend_cap"), \
         patch.object(memory_service.llm, "record_spend"), \
         patch.object(memory_service.llm, "chat",
                      return_value=(_json.dumps({"struggle_topics": ["fractions"]}), 100, 20)):
        memory_service.regenerate(student_id, 8, "student: fractions\nassistant: chalo dekhte hain")

    verify = SessionLocal()
    try:
        stored = verify.execute(_t("SELECT summary_jsonb FROM student_memory WHERE student_id = :s"),
                                {"s": str(student_id)}).scalar_one()
    finally:
        verify.close()
    assert stored["struggle_topics"] == ["fractions"]
