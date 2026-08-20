"""Photo questions.

The property under test throughout is that a photo is an INPUT METHOD: it becomes
text, the text goes through the one existing pipeline, and the image reaches
neither the tutoring model nor any store.
"""
import io
from unittest.mock import patch

import pytest

from app.modules.ai_orchestrator import vision
from app.modules.conversation import router as conv_router
from tests.conftest import make_student

JPEG = b"\xff\xd8\xff" + b"\x00" * 400
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 400


def _photo(client, token, blob=JPEG, mime="image/jpeg", **form):
    return client.post(
        "/v1/conversations/photo",
        files={"photo": ("q.jpg", io.BytesIO(blob), mime)},
        data=form,
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.fixture
def photo_on():
    with patch.object(conv_router, "PHOTO_QUESTIONS_ENABLED", True):
        yield


# --------------------------------------------------------------------------
#  The flag
# --------------------------------------------------------------------------

def test_disabled_by_default_returns_404_not_501(client, db):
    """Off unless explicitly enabled, and silent about existing.

    404 rather than 501 on purpose: a 501 tells a scanner there is an image
    endpoint here that might be switched on later.
    """
    _, _, token = make_student(db)
    assert _photo(client, token).status_code == 404


# --------------------------------------------------------------------------
#  Rejections that must happen BEFORE any paid call
# --------------------------------------------------------------------------

def test_oversized_photo_rejected_without_calling_openai(client, db, photo_on):
    _, _, token = make_student(db)
    with patch.object(conv_router, "MAX_IMAGE_BYTES", 100), \
         patch.object(vision.llm, "moderate_image_url") as mod, \
         patch.object(vision.llm, "chat_vision") as vis:
        resp = _photo(client, token, blob=JPEG + b"\x00" * 5000)
    assert resp.status_code == 413
    mod.assert_not_called()
    vis.assert_not_called()


def test_non_image_content_type_rejected_without_calling_openai(client, db, photo_on):
    _, _, token = make_student(db)
    with patch.object(vision.llm, "moderate_image_url") as mod, \
         patch.object(vision.llm, "chat_vision") as vis:
        resp = _photo(client, token, blob=b"%PDF-1.7", mime="application/pdf")
    assert resp.status_code == 422
    mod.assert_not_called()
    vis.assert_not_called()


def test_mislabelled_file_rejected_on_magic_bytes(client, db, photo_on):
    """A declared Content-Type is a claim. This is a zip wearing a .jpg name."""
    _, _, token = make_student(db)
    with patch.object(vision.llm, "moderate_image_url") as mod, \
         patch.object(vision.llm, "chat_vision") as vis:
        resp = _photo(client, token, blob=b"PK\x03\x04" + b"\x00" * 200)
    assert resp.status_code == 422
    mod.assert_not_called()
    vis.assert_not_called()


# --------------------------------------------------------------------------
#  Moderation is blocking, and comes before transcription
# --------------------------------------------------------------------------

def test_flagged_image_never_reaches_the_vision_model(client, db, photo_on):
    """The whole point of moderating before transcribing.

    A camera pointed at homework also catches faces and rooms. If a flagged image
    were transcribed first, the flagged content would already have been sent to a
    generation model by the time anyone noticed.
    """
    _, student, token = make_student(db)
    with patch.object(vision.llm, "moderate_image_url", return_value=True), \
         patch.object(vision.llm, "chat_vision") as vis:
        resp = _photo(client, token)
    assert resp.status_code == 422
    vis.assert_not_called()

    from sqlalchemy import text as sql
    row = db.execute(
        sql("SELECT direction, content FROM moderation_flags WHERE student_id = :s"),
        {"s": student.id},
    ).first()
    assert row is not None, "a blocked photo must leave a reviewable record"
    assert row[0] == "inbound"
    # The record names the event, not the picture — there is no picture to name.
    assert "photo" in row[1]


# --------------------------------------------------------------------------
#  The happy path
# --------------------------------------------------------------------------

def test_transcribed_text_becomes_the_student_message(client, db, photo_on):
    """The image is transcribed, then the ordinary pipeline runs on that text."""
    _, student, token = make_student(db, grade=8)
    with patch.object(vision.llm, "moderate_image_url", return_value=False), \
         patch.object(vision.llm, "chat_vision",
                      return_value=("A gun recoils when fired. Why?", 300, 12)), \
         patch.object(conv_router.service, "handle_student_message") as handled:
        handled.return_value = (
            conv_router.service.TurnResult("Newton ka third law!", 50, "m", "grounded"),
            student.id, None,
        )
        resp = _photo(client, token)

    assert resp.status_code == 200
    # The pipeline received TEXT, not bytes.
    assert handled.call_args.args[3] == "A gun recoils when fired. Why?"
    assert handled.call_args.kwargs["source"] == "photo"
    # The client is told what was read, so a misread can be corrected by the child.
    assert resp.json()["transcribed_text"] == "A gun recoils when fired. Why?"


def test_unreadable_photo_asks_for_a_retake_not_a_refusal(client, db, photo_on):
    """A blurry page must not become an empty query that retrieval then refuses.

    Those are different failures needing different messages: "I could not read
    that" is actionable, "not in your textbook" is wrong and confusing.
    """
    _, _, token = make_student(db)
    with patch.object(vision.llm, "moderate_image_url", return_value=False), \
         patch.object(vision.llm, "chat_vision", return_value=("NO_QUESTION_FOUND", 300, 5)), \
         patch.object(conv_router.service, "handle_student_message") as handled:
        resp = _photo(client, token)
    assert resp.status_code == 422
    assert "photo" in resp.json()["detail"].lower()
    handled.assert_not_called()


def test_png_is_accepted(client, db, photo_on):
    _, student, token = make_student(db)
    with patch.object(vision.llm, "moderate_image_url", return_value=False), \
         patch.object(vision.llm, "chat_vision", return_value=("What is pressure?", 300, 6)), \
         patch.object(conv_router.service, "handle_student_message") as handled:
        handled.return_value = (
            conv_router.service.TurnResult("Pressure...", 40, "m", "grounded"), student.id, None,
        )
        resp = _photo(client, token, blob=PNG, mime="image/png")
    assert resp.status_code == 200


# --------------------------------------------------------------------------
#  Authorization
# --------------------------------------------------------------------------

def test_parent_cannot_send_a_photo(client, db, photo_on):
    from tests.conftest import make_parent
    _, token = make_parent(db)
    with patch.object(vision.llm, "moderate_image_url") as mod:
        resp = _photo(client, token)
    assert resp.status_code == 403
    mod.assert_not_called()


def test_unauthenticated_photo_rejected(client, photo_on):
    resp = client.post(
        "/v1/conversations/photo",
        files={"photo": ("q.jpg", io.BytesIO(JPEG), "image/jpeg")},
    )
    assert resp.status_code == 401


# --------------------------------------------------------------------------
#  The transcription prompt
# --------------------------------------------------------------------------

def test_prompt_forbids_answering():
    """The model must read the question, never answer it. If this instruction
    is ever softened, the image becomes an answer source and every grounding
    guarantee downstream stops meaning anything."""
    p = vision.TRANSCRIBE_PROMPT
    assert "Never answer" in p
    assert "Transcribe ONLY" in p
    assert vision.NO_QUESTION in p


def test_transcription_token_cap_is_far_below_a_tutoring_reply():
    """A cap this size is what makes 'it started answering' visible as truncation
    rather than as a plausible long transcription."""
    from app.config import MAX_OUTPUT_TOKENS
    assert vision.TRANSCRIBE_MAX_TOKENS < MAX_OUTPUT_TOKENS
