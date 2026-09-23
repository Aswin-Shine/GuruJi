import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from unittest.mock import MagicMock

import httpx
from sqlalchemy import text as _t

from app.config import WHATSAPP_APP_SECRET
from app.modules.ai_orchestrator.orchestrator import FALLBACK_UNAVAILABLE, TurnResult
from app.modules.conversation import router as conv_router
from app.modules.conversation import service
from app.modules.whatsapp import service as whatsapp
from app.modules.whatsapp.service import mark_read_typing as _real_mark_read_typing
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


def _phone_of(db, student) -> str:
    return db.execute(_t("SELECT phone_number FROM users WHERE id = ("
                         "SELECT user_id FROM students WHERE id = :s)"), {"s": str(student.id)}).scalar_one()


def _claimed(db, msg_id: str) -> bool:
    return db.execute(_t("SELECT count(*) FROM processed_webhook_messages WHERE whatsapp_message_id = :i"),
                      {"i": msg_id}).scalar_one() == 1


def _fresh_phone() -> str:
    """A number no earlier test (or earlier RUN — the database persists between
    runs) has ever onboarded. A fixed literal here passed once and then failed
    forever, because the second run found it already a student."""
    import uuid as _uuid
    return f"+9190{_uuid.uuid4().int % 10**8:08d}"


def _outbound_configured(post_mock):
    """Patch the sender into a configured state with httpx.post replaced.

    Module attributes, not env: config is read once at import, so the sender's own
    names are what its functions actually consult."""
    return (
        patch.object(whatsapp, "WHATSAPP_ACCESS_TOKEN", "tok"),
        patch.object(whatsapp, "WHATSAPP_PHONE_NUMBER_ID", "123"),
        patch.object(whatsapp, "WHATSAPP_GRAPH_API_VERSION", "v0.0"),
        patch.object(whatsapp, "RETRY_DELAY_S", 0),
        patch.object(whatsapp.httpx, "post", post_mock),
    )


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

    assert first.status_code == 200 and first.json()["status"] == "accepted"
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
    with patch.object(service, "orchestrate", return_value=_turn("ok", 5, "test-model")) as mock_orch:
        resp = _post_webhook(client, _msg_payload(phone, "gravity samjhao", msg_id=None))
    assert resp.status_code == 200 and resp.json()["status"] == "accepted"
    assert mock_orch.call_count == 1


def test_webhook_rejects_oversized_message(client, db):
    """webhook had no length cap while send_message() did."""
    from sqlalchemy import text as _t
    import uuid as _uuid
    _, student, _ = make_student(db)
    phone = db.execute(_t("SELECT phone_number FROM users WHERE id = ("
                          "SELECT user_id FROM students WHERE id = :s)"), {"s": str(student.id)}).scalar_one()
    with patch.object(service, "orchestrate") as mock_orch, \
         patch.object(conv_router.whatsapp, "send_text") as mock_send:
        resp = _post_webhook(client, _msg_payload(phone, "x" * 2001, f"wamid.{_uuid.uuid4()}"))
    assert resp.status_code == 200 and resp.json()["status"] == "accepted"
    mock_orch.assert_not_called()
    mock_send.assert_called_once_with(phone, conv_router.TOO_LONG)


def test_webhook_rejects_empty_message(client, db):
    from sqlalchemy import text as _t
    import uuid as _uuid
    _, student, _ = make_student(db)
    phone = db.execute(_t("SELECT phone_number FROM users WHERE id = ("
                          "SELECT user_id FROM students WHERE id = :s)"), {"s": str(student.id)}).scalar_one()
    with patch.object(service, "orchestrate") as mock_orch, \
         patch.object(conv_router.whatsapp, "send_text") as mock_send:
        resp = _post_webhook(client, _msg_payload(phone, "   \n  ", f"wamid.{_uuid.uuid4()}"))
    assert resp.status_code == 200 and resp.json()["status"] == "accepted"
    mock_orch.assert_not_called()
    mock_send.assert_called_once_with(phone, conv_router.TOO_LONG)


# ---- outbound delivery + async ack --------------------------------------------


def test_webhook_acks_before_processing_and_never_returns_the_reply(client, db):
    """The body is an ack. Meta retries anything slower than ~3-5s and a turn is
    routinely longer, so processing is scheduled, not inline. Patching the task
    proves the handler itself never reaches the model."""
    import uuid as _uuid
    _, student, _ = make_student(db)
    phone = _phone_of(db, student)
    msg_id = f"wamid.{_uuid.uuid4()}"
    with patch.object(conv_router, "_process_inbound_task") as mock_task, \
         patch.object(service, "orchestrate") as mock_orch:
        resp = _post_webhook(client, _msg_payload(phone, "gravity samjhao", msg_id))
    assert resp.status_code == 200
    assert resp.json() == {"status": "accepted", "accepted": 1}  # no reply key, ever
    mock_task.assert_called_once_with(phone, "gravity samjhao", msg_id)
    mock_orch.assert_not_called()


def test_duplicate_never_schedules_the_task(client, db):
    import uuid as _uuid
    _, student, _ = make_student(db)
    phone = _phone_of(db, student)
    payload = _msg_payload(phone, "hi", f"wamid.{_uuid.uuid4()}")
    with patch.object(conv_router, "_process_inbound_task") as mock_task:
        _post_webhook(client, payload)
        second = _post_webhook(client, payload)
    assert second.json()["status"] == "duplicate"
    assert mock_task.call_count == 1


def test_reply_is_sent_through_meta_cloud_api(client, db):
    """Exact request shape: URL from version + phone_number_id, bearer header,
    Meta's text envelope, `to` without a leading +."""
    import uuid as _uuid
    _, student, _ = make_student(db)
    phone = _phone_of(db, student)
    post = MagicMock(return_value=MagicMock(status_code=200))
    a, b, c, d, e = _outbound_configured(post)
    with a, b, c, d, e, patch.object(service, "orchestrate", return_value=_turn("Bilkul sahi!", 10, "m")):
        resp = _post_webhook(client, _msg_payload(phone, "5 x 7?", f"wamid.{_uuid.uuid4()}"))
    assert resp.json() == {"status": "accepted", "accepted": 1}
    post.assert_called_once()
    args, kwargs = post.call_args
    assert args[0] == "https://graph.facebook.com/v0.0/123/messages"
    assert kwargs["headers"] == {"Authorization": "Bearer tok"}
    assert kwargs["json"] == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": phone.lstrip("+"),
        "type": "text",
        "text": {"preview_url": False, "body": "Bilkul sahi!"},
    }
    assert not phone.lstrip("+").startswith("+") and kwargs["json"]["to"] == phone.lstrip("+")


def test_onboarding_prompts_are_sent_not_just_the_tutoring_reply(client, db):
    """Every branch that has words for the student must deliver them: on WhatsApp
    there is no other surface to show an onboarding prompt on."""
    import uuid as _uuid
    fresh = _fresh_phone()
    post = MagicMock(return_value=MagicMock(status_code=200))
    a, b, c, d, e = _outbound_configured(post)
    with a, b, c, d, e, patch.object(conv_router, "ALLOWED_PHONE_NUMBERS", []):
        _post_webhook(client, _msg_payload(fresh, "hi", f"wamid.{_uuid.uuid4()}"))
        _post_webhook(client, _msg_payload(fresh, "8", f"wamid.{_uuid.uuid4()}"))
    bodies = [c.kwargs["json"]["text"]["body"] for c in post.call_args_list]
    assert bodies == [conv_router.ONBOARD_ASK_GRADE, conv_router.ONBOARD_DONE.format(grade=8)]
    assert all(c.kwargs["json"]["to"] == fresh.lstrip("+") for c in post.call_args_list)


def test_allowlist_refusal_is_sent_to_the_stranger(client, db):
    import uuid as _uuid
    stranger = _fresh_phone()
    post = MagicMock(return_value=MagicMock(status_code=200))
    a, b, c, d, e = _outbound_configured(post)
    with a, b, c, d, e, patch.object(conv_router, "ALLOWED_PHONE_NUMBERS", ["+919999999999"]):
        _post_webhook(client, _msg_payload(stranger, "hi", f"wamid.{_uuid.uuid4()}"))
    post.assert_called_once()
    assert post.call_args.kwargs["json"]["text"]["body"] == conv_router.NOT_INVITED
    assert db.execute(_t("SELECT count(*) FROM users WHERE phone_number = :p"), {"p": stranger}).scalar_one() == 0


def test_unconfigured_outbound_logs_only_and_still_persists_the_turn(client, db):
    """No Meta account: the turn completes and is stored, nothing leaves the box."""
    import uuid as _uuid
    _, student, _ = make_student(db)
    phone = _phone_of(db, student)
    with patch.object(whatsapp, "WHATSAPP_ACCESS_TOKEN", ""), \
         patch.object(whatsapp, "WHATSAPP_PHONE_NUMBER_ID", ""), \
         patch.object(whatsapp, "WHATSAPP_GRAPH_API_VERSION", ""), \
         patch.object(whatsapp.httpx, "post") as post, \
         patch.object(service, "orchestrate", return_value=_turn("ok", 5, "m")):
        resp = _post_webhook(client, _msg_payload(phone, "gravity samjhao", f"wamid.{_uuid.uuid4()}"))
    assert resp.json()["status"] == "accepted"
    post.assert_not_called()
    count = db.execute(_t(
        "SELECT count(*) FROM messages m JOIN conversations c ON c.id = m.conversation_id "
        "WHERE c.student_id = :s AND m.sender = 'assistant'"), {"s": str(student.id)}).scalar_one()
    assert count == 1


def test_send_transport_failure_retries_once_and_keeps_the_claim(client, db):
    """A delivery failure is not a processing failure: the reply is already in
    `messages`, so the claim stays and Meta's (rare) retry is still deduped."""
    import uuid as _uuid
    _, student, _ = make_student(db)
    phone = _phone_of(db, student)
    msg_id = f"wamid.{_uuid.uuid4()}"
    post = MagicMock(side_effect=httpx.ConnectError("boom"))
    a, b, c, d, e = _outbound_configured(post)
    with a, b, c, d, e, patch.object(service, "orchestrate", return_value=_turn("ok", 5, "m")):
        resp = _post_webhook(client, _msg_payload(phone, "gravity samjhao", msg_id))
    assert resp.status_code == 200 and resp.json()["status"] == "accepted"
    assert post.call_count == 2
    assert _claimed(db, msg_id)
    count = db.execute(_t(
        "SELECT count(*) FROM messages m JOIN conversations c ON c.id = m.conversation_id "
        "WHERE c.student_id = :s AND m.sender = 'assistant'"), {"s": str(student.id)}).scalar_one()
    assert count == 1


def test_send_4xx_does_not_retry(client, db):
    """131047 (outside the 24h window) fails identically 1.5s later — one call."""
    import uuid as _uuid
    _, student, _ = make_student(db)
    phone = _phone_of(db, student)
    msg_id = f"wamid.{_uuid.uuid4()}"
    failed = MagicMock(status_code=400)
    failed.json.return_value = {"error": {"code": 131047, "message": "Re-engagement message"}}
    post = MagicMock(return_value=failed)
    a, b, c, d, e = _outbound_configured(post)
    with a, b, c, d, e, patch.object(service, "orchestrate", return_value=_turn("ok", 5, "m")):
        resp = _post_webhook(client, _msg_payload(phone, "gravity samjhao", msg_id))
    assert resp.json()["status"] == "accepted"
    assert post.call_count == 1
    assert _claimed(db, msg_id)


def test_send_5xx_retries_once(client, db):
    import uuid as _uuid
    _, student, _ = make_student(db)
    phone = _phone_of(db, student)
    post = MagicMock(side_effect=[MagicMock(status_code=503), MagicMock(status_code=200)])
    a, b, c, d, e = _outbound_configured(post)
    with a, b, c, d, e, patch.object(service, "orchestrate", return_value=_turn("ok", 5, "m")):
        _post_webhook(client, _msg_payload(phone, "gravity samjhao", f"wamid.{_uuid.uuid4()}"))
    assert post.call_count == 2


def test_processing_crash_releases_claim_and_tells_the_student(client, db):
    """Once the 200 has gone out Meta will not retry, so the student must hear
    something — the honest network-slow fallback — and the claim must not record a
    message that was never actually processed."""
    import uuid as _uuid
    _, student, _ = make_student(db)
    phone = _phone_of(db, student)
    msg_id = f"wamid.{_uuid.uuid4()}"
    with patch.object(conv_router.service, "handle_student_message", side_effect=RuntimeError("db exploded")), \
         patch.object(conv_router.whatsapp, "send_text") as mock_send:
        resp = _post_webhook(client, _msg_payload(phone, "gravity samjhao", msg_id))
    assert resp.status_code == 200 and resp.json()["status"] == "accepted"
    mock_send.assert_called_once_with(phone, FALLBACK_UNAVAILABLE)
    assert not _claimed(db, msg_id)


def test_batched_payload_processes_every_message_in_order(client, db):
    """Meta may batch messages across entries/changes. Reading only messages[0]
    dropped the rest silently; every one must be claimed and answered, in order."""
    import uuid as _uuid
    _, student, _ = make_student(db)
    phone = _phone_of(db, student)
    ids = [f"wamid.{_uuid.uuid4()}" for _ in range(3)]
    msg = lambda i, t: {"id": i, "from": phone, "type": "text", "text": {"body": t}}  # noqa: E731
    payload = {"entry": [
        {"changes": [{"value": {"messages": [msg(ids[0], "pehla"), msg(ids[1], "doosra")]}}]},
        {"changes": [{"value": {"statuses": [{"id": "x"}]}}, {"value": {"messages": [msg(ids[2], "teesra")]}}]},
    ]}
    with patch.object(service, "orchestrate", side_effect=lambda *a, **k: _turn("ok")) as mock_orch, \
         patch.object(conv_router.whatsapp, "send_text") as mock_send:
        resp = _post_webhook(client, payload)
        again = _post_webhook(client, payload)
    assert resp.json() == {"status": "accepted", "accepted": 3}
    assert again.json() == {"status": "duplicate", "accepted": 0}
    assert mock_orch.call_count == 3 and mock_send.call_count == 3
    assert all(_claimed(db, i) for i in ids)
    sent_in = db.execute(_t(
        "SELECT m.content FROM messages m JOIN conversations c ON c.id = m.conversation_id "
        "WHERE c.student_id = :s AND m.sender = 'student' ORDER BY m.created_at"), {"s": str(student.id)}).scalars().all()
    assert sent_in == ["pehla", "doosra", "teesra"]


def test_memory_failure_after_send_does_not_send_the_fallback(client, db):
    """Once the reply is out, a later failure (memory regeneration) must not follow
    it with 'network slow' nor release the claim of a turn that did complete."""
    import uuid as _uuid
    _, student, _ = make_student(db)
    phone = _phone_of(db, student)
    msg_id = f"wamid.{_uuid.uuid4()}"
    regen = service.RegenArgs(student.id, student.grade, "t")
    with patch.object(conv_router.service, "handle_student_message", return_value=(_turn("Jawab"), None, regen)), \
         patch.object(conv_router.memory, "regenerate", side_effect=RuntimeError("db down")), \
         patch.object(conv_router.whatsapp, "send_text") as mock_send:
        _post_webhook(client, _msg_payload(phone, "gravity samjhao", msg_id))
    mock_send.assert_called_once_with(phone, "Jawab")
    assert _claimed(db, msg_id)


def test_web_and_whatsapp_resolve_to_the_same_account(client, db):
    """The web client sends +91..., Meta sends 91... — one student, one account."""
    _, student, _ = make_student(db)
    phone = _phone_of(db, student)
    with patch.object(service, "orchestrate", return_value=_turn("ok")), \
         patch.object(conv_router.whatsapp, "send_text"):
        resp = _post_webhook(client, _msg_payload(phone.lstrip("+"), "gravity samjhao", f"wamid.{phone}.n"))
    assert resp.json()["accepted"] == 1
    n = db.execute(_t("SELECT count(*) FROM users WHERE phone_number IN (:a, :b)"),
                   {"a": phone, "b": phone.lstrip("+")}).scalar_one()
    assert n == 1
    convs = db.execute(_t("SELECT count(*) FROM conversations WHERE student_id = :s AND channel = 'whatsapp'"),
                       {"s": str(student.id)}).scalar_one()
    assert convs == 1


def test_ignored_webhook_logs_its_shape_without_pii(client, caplog):
    """An ignored POST must say WHY in the log (status receipt vs unknown message
    shape), and must never log the phone number or message text."""
    import logging
    caplog.set_level(logging.INFO, logger="guruji.webhook")
    status = {"entry": [{"changes": [{"field": "messages", "value": {
        "statuses": [{"id": "wamid.s", "status": "delivered", "recipient_id": "919000000001"}]}}]}]}
    odd = {"entry": [{"changes": [{"field": "messages", "value": {"messages": [
        {"id": "wamid.o", "from_user_id": "IN.123", "type": "text", "text": {"body": "secret question"}}]}}]}]}
    assert _post_webhook(client, status).json() == {"status": "ignored"}
    assert _post_webhook(client, odd).json() == {"status": "ignored"}
    logs = caplog.text
    assert "keys=['statuses']" in logs
    assert "unexpected shape keys=['from_user_id', 'id', 'text', 'type']" in logs
    assert "919000000001" not in logs and "secret question" not in logs


def _wa(client, phone, text, msg=None):
    """Post one WhatsApp message and return every reply sent_text was asked to send."""
    import uuid as _uuid
    msg = msg or {"from": phone, "type": "text", "text": {"body": text}}
    msg.setdefault("id", f"wamid.{_uuid.uuid4()}")
    with patch.object(conv_router.whatsapp, "send_text") as mock_send, \
         patch.object(service, "orchestrate", return_value=_turn("model-answer")) as mock_orch:
        _post_webhook(client, {"entry": [{"changes": [{"value": {"messages": [msg]}}]}]})
    return [c.args[1] for c in mock_send.call_args_list], mock_orch.call_count


def test_greeting_confirms_class_without_a_model_call(client, db):
    """'hi' must settle the class before any subject talk, and costs nothing."""
    _, student, _ = make_student(db, grade=8)
    phone = _phone_of(db, student)
    for hello in ("hi", "Hii!", "hello guruji", "Namaste 🙏", "good morning"):
        sent, model_calls = _wa(client, phone, hello)
        assert sent == [conv_router.GREET.format(grade=8)], hello
        assert model_calls == 0
    # A real question that merely starts with "hi" still goes to the tutor.
    sent, model_calls = _wa(client, phone, "hi, pressure kya hota hai?")
    assert sent == ["model-answer"] and model_calls == 1


def test_class_command_really_changes_the_class(client, db):
    """The model used to reply 'Class 6 set!' while nothing changed."""
    _, student, _ = make_student(db, grade=8)
    phone = _phone_of(db, student)
    sent, model_calls = _wa(client, phone, "Class 6 CBSE")
    assert sent == [conv_router.CLASS_CHANGED.format(grade=6)] and model_calls == 0
    db.expire_all()
    assert db.execute(_t("SELECT grade FROM students WHERE id = :s"), {"s": str(student.id)}).scalar_one() == 6
    for text_in, grade in (("class 7th", 7), ("meri class 9 hai", 9)):
        assert _wa(client, phone, text_in)[0] == [conv_router.CLASS_CHANGED.format(grade=grade)]
    # A bare number mid-conversation is an answer to GuruJi's question, not a class change.
    sent, model_calls = _wa(client, phone, "6")
    assert sent == ["model-answer"] and model_calls == 1
    assert db.execute(_t("SELECT grade FROM students WHERE id = :s"), {"s": str(student.id)}).scalar_one() == 9
    # Out of range is not a command.
    assert _wa(client, phone, "class 12")[1] == 1


def test_onboarding_accepts_class_phrasings(client, db):
    for answer, grade in (("class 8", 8), ("7th", 7), ("10", 10)):
        fresh = _fresh_phone()
        with patch.object(conv_router, "ALLOWED_PHONE_NUMBERS", []):
            assert _wa(client, fresh, "hi")[0] == [conv_router.ONBOARD_ASK_GRADE]
            assert _wa(client, fresh, answer)[0] == [conv_router.ONBOARD_DONE.format(grade=grade)]


def test_photo_or_voice_gets_a_type_it_reply_and_reactions_are_ignored(client, db):
    _, student, _ = make_student(db)
    phone = _phone_of(db, student)
    for media in ({"type": "image", "image": {"id": "m1", "caption": "q3"}},
                  {"type": "audio", "audio": {"id": "m2", "voice": True}},
                  {"type": "sticker", "sticker": {"id": "m3"}}):
        sent, model_calls = _wa(client, phone, None, {"from": phone, **media})
        assert sent == [conv_router.NOT_TEXT] and model_calls == 0
    sent, model_calls = _wa(client, phone, None, {"from": phone, "type": "reaction",
                                                  "reaction": {"message_id": "x", "emoji": "👍"}})
    assert sent == [] and model_calls == 0


def test_typing_indicator_shape_and_never_raises():
    """Blue ticks + typing while the 5-8s turn runs; a Meta failure must not
    break the turn."""
    post = MagicMock(return_value=MagicMock(status_code=200))
    a, b, c, d, e = _outbound_configured(post)
    with a, b, c, d, e:
        _real_mark_read_typing("wamid.T")
    args, kwargs = post.call_args
    assert args[0] == "https://graph.facebook.com/v0.0/123/messages"
    assert kwargs["json"] == {"messaging_product": "whatsapp", "status": "read",
                              "message_id": "wamid.T", "typing_indicator": {"type": "text"}}
    a, b, c, d, e = _outbound_configured(MagicMock(side_effect=httpx.ConnectError("down")))
    with a, b, c, d, e:
        _real_mark_read_typing("wamid.T")  # must not raise
    _real_mark_read_typing("wamid.T")  # unconfigured: no-op, no network


def test_send_text_never_raises_even_when_the_client_explodes():
    """The sender is called after the reply is persisted; nothing it does may
    unwind the turn."""
    a, b, c, d, e = _outbound_configured(MagicMock(side_effect=RuntimeError("weird")))
    with a, b, c, d, e:
        assert whatsapp.send_text("+919000000001", "hi") is False


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
