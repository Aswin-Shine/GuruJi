"""Two auth mechanisms, deliberately separate:
- REST routes: bearer token via get_current_user (role + ownership).
- WhatsApp webhook: Meta HMAC signature (X-Hub-Signature-256) + phone lookup.
  No bearer token — Meta calls it directly."""
import hashlib
import hmac
import json
import logging
import uuid

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from sqlalchemy.orm import Session

from sqlalchemy import text

from app.config import (
    ALLOWED_PHONE_NUMBERS,
    MAX_IMAGE_BYTES,
    PHOTO_QUESTIONS_ENABLED,
    WHATSAPP_APP_SECRET,
    WHATSAPP_VERIFY_TOKEN,
)
from app.db.session import get_db
from app.modules.conversation import service
from app.modules.conversation.schemas import ConversationOut, MessageOut, SendMessageIn, SendMessageOut
from app.modules.identity import service as identity
from app.modules.ai_orchestrator import llm, vision
from app.modules.ai_orchestrator.orchestrator import FALLBACK_MODERATED
from app.modules.safety import service as safety
from app.modules.memory import service as memory
from app.modules.identity.dependencies import CurrentUser, get_current_user
from app.modules.student_profile import service as profile

router = APIRouter(prefix="/v1", tags=["conversation"])
log = logging.getLogger("guruji.webhook")

ONBOARD_ASK_GRADE = "Namaste! Main GuruJi hoon 🙏 Pehle apni class batao (5 se 10 tak ka number bhejo)."
ONBOARD_DONE = "Class {grade} — set! Ab koi bhi doubt poochho, main hoon na. 📚"
TOO_LONG = "Arre, itna lamba message! Thoda chhota karke bhejo — ek sawaal ek baar. 🙏"
MAX_INBOUND_CHARS = 2000  # same ceiling send_message() already enforces
NOT_INVITED = "Namaste! GuruJi abhi sirf pilot students ke liye hai. 🙏"


@router.get("/webhooks/whatsapp")
def webhook_verify(
    mode: str = Query("", alias="hub.mode"),
    token: str = Query("", alias="hub.verify_token"),
    challenge: str = Query("", alias="hub.challenge"),
) -> int | dict:
    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        return int(challenge)
    raise HTTPException(status_code=403, detail="Verify token mismatch")


def _verify_signature(body: bytes, signature_header: str) -> bool:
    if not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(WHATSAPP_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature_header.removeprefix("sha256="), expected)


def _extract_message(payload: dict) -> tuple[str, str, str | None] | None:
    """Returns (phone, text, whatsapp_message_id) or None for non-text/status events.

    The message id may be absent on malformed or non-standard payloads. That is NOT a
    rejection — dedupe is simply skipped."""
    try:
        msg = payload["entry"][0]["changes"][0]["value"]["messages"][0]
        if msg.get("type") != "text":
            return None
        return msg["from"], msg["text"]["body"], msg.get("id")
    except (KeyError, IndexError):
        return None


def _claim_message(db: Session, message_id: str) -> bool:
    """Claim a WhatsApp message id. True = ours to process, False = duplicate.

    Meta delivers at-least-once and retries anything slower than its ~3-5s ack window,
    which this synchronous pipeline routinely is. Without this, a retry buys a second
    billed LLM call and a duplicate row in `messages`."""
    inserted = db.execute(
        text("INSERT INTO processed_webhook_messages (whatsapp_message_id) VALUES (:i) ON CONFLICT DO NOTHING"),
        {"i": message_id},
    ).rowcount
    db.commit()
    return inserted == 1


def _release_message(db: Session, message_id: str) -> None:
    """Undo the claim when processing raises.

    Without this, a crash after the claim means Meta's retry is deduped away and the
    student's question is lost silently — a worse failure than the duplicate the claim
    exists to prevent."""
    try:
        db.rollback()
        db.execute(text("DELETE FROM processed_webhook_messages WHERE whatsapp_message_id = :i"), {"i": message_id})
        db.commit()
    except Exception as exc:  # never mask the original error
        log.error("failed to release webhook claim id=%s: %s", message_id, exc)


async def _raw_body(request: Request) -> bytes:
    return await request.body()


@router.post("/webhooks/whatsapp")
def webhook_inbound(
    background_tasks: BackgroundTasks,
    body: bytes = Depends(_raw_body),
    signature: str = Header("", alias="X-Hub-Signature-256"),
    db: Session = Depends(get_db),
) -> dict:
    """Inbound WhatsApp message.

    Deliberately a plain `def`, so Starlette runs it on the thread pool. As `async def`
    calling only blocking I/O (sync SQLAlchemy, sync OpenAI client) it would serialise
    all webhook traffic on the one event-loop thread. A sync handler cannot
    `await request.body()`, hence the small async dependency above — signature
    verification needs the raw bytes, so they are read once and reused."""
    if not _verify_signature(body, signature):
        raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    extracted = _extract_message(payload)
    if extracted is None:
        return {"status": "ignored"}
    phone, text_in, message_id = extracted

    # Gates everything below: a duplicate must never reach the point where a second
    # LLM call is billed or a second memory-regeneration task is scheduled.
    if message_id is None:
        log.warning("webhook message missing id, dedupe skipped")
    elif not _claim_message(db, message_id):
        return {"status": "duplicate", "reply": None}

    try:
        return _process_inbound(db, background_tasks, phone, text_in)
    except Exception:
        if message_id is not None:
            _release_message(db, message_id)
        raise


def _process_inbound(db: Session, background_tasks: BackgroundTasks, phone: str, text_in: str) -> dict:
    # Same length ceiling send_message() enforces. Returns 200 with a status body,
    # never an HTTPException — Meta requires a 200 here regardless of outcome.
    if not text_in.strip() or len(text_in) > MAX_INBOUND_CHARS:
        return {"status": "rejected", "reply": TOO_LONG}

    # Checked BEFORE get_or_create_user, which provisions an account on first contact,
    # so an unknown number must never reach it. Empty list = open.
    if ALLOWED_PHONE_NUMBERS and phone.lstrip("+") not in {p.lstrip("+") for p in ALLOWED_PHONE_NUMBERS}:
        log.warning("inbound from non-allowlisted number, dropped")
        return {"status": "not_allowed", "reply": NOT_INVITED}

    user = identity.get_or_create_user(db, phone, "student")
    if user.role != "student":
        return {"status": "ignored", "reply": None}
    if service.rate_limited(user.id):
        return {"status": "rate_limited", "reply": "Thoda dheere, dost! Ek minute ruk ke phir poochho. 😅"}

    student = profile.get_student_by_user(db, user.id)
    if student is None:
        # Deliberate simplification: onboarding collects ONLY the class, because grade
        # is the one field retrieval requires to function. A multi-question flow needs
        # per-user turn-state tracking, which is not built yet.
        stripped = text_in.strip()
        if stripped.isdigit() and 5 <= int(stripped) <= 10:
            student = profile.create_student(db, user.id, int(stripped), "NCERT", "hinglish")
            return {"status": "onboarded", "reply": ONBOARD_DONE.format(grade=student.grade)}
        return {"status": "onboarding", "reply": ONBOARD_ASK_GRADE}

    turn, _, regen = service.handle_student_message(db, student, "whatsapp", text_in)
    if regen is not None:
        # Off the request path. regenerate() opens its own session, because the
        # request's is closed by get_db() the moment this response returns.
        background_tasks.add_task(memory.regenerate, regen.student_id, regen.grade, regen.transcript)
    # NOTE: the reply is logged and returned in the webhook response body. Meta does
    # not deliver that body to the user — real outbound send is not implemented yet.
    log.info("outbound_reply phone=%s grounding=%s reply=%r", phone, turn.grounding, turn.reply)
    return {"status": "ok", "reply": turn.reply}


@router.post("/conversations/messages", response_model=SendMessageOut, tags=["web-client"])
def send_message(
    body: SendMessageIn,
    background_tasks: BackgroundTasks,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SendMessageOut:
    """Web-channel send. Same orchestration as the webhook — calls the one
    handle_student_message(), never a second code path. student_id comes from the
    authenticated token ONLY (IDOR guard), never the request body."""
    if current.role != "student" or current.student_id is None:
        raise HTTPException(status_code=403, detail="Students only")
    text_in = body.text.strip()
    if not text_in or len(text_in) > 2000:
        raise HTTPException(status_code=422, detail="Message must be 1-2000 characters")
    if service.rate_limited(current.user_id):
        # 429, not the webhook's 200-with-status shape — that exists only because
        # Meta requires 200s; a web client gets a real status code.
        raise HTTPException(status_code=429, detail="Thoda dheere, dost! Ek minute ruk ke phir poochho.")
    student = profile.get_student(db, current.student_id)
    if student is None:
        raise HTTPException(status_code=404, detail="Student profile not found")
    if body.new_session:
        service.close_open_sessions(db, student.id, "web")
    try:
        turn, conv_id, regen = service.handle_student_message(
            db, student, "web", text_in,
            force_new=body.new_session, target_id=body.conversation_id,
            grade=body.grade, subject=body.subject,
        )
    except LookupError:
        # The id is not this student's, or is hidden. 404 rather than 403 on purpose:
        # a 403 would confirm the conversation exists, which is itself a small leak.
        raise HTTPException(status_code=404, detail="Conversation not found")
    if regen is not None:  # scheduled, not inline
        background_tasks.add_task(memory.regenerate, regen.student_id, regen.grade, regen.transcript)
    return SendMessageOut(
        conversation_id=conv_id, reply=turn.reply,
        grounding=turn.grounding, citation=turn.citation,
        source_excerpt=turn.source_excerpt,
    )


@router.post("/conversations/photo", response_model=SendMessageOut, tags=["web-client"])
def send_photo(
    background_tasks: BackgroundTasks,
    photo: UploadFile = File(...),
    new_session: bool = Form(False),
    conversation_id: uuid.UUID | None = Form(None),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SendMessageOut:
    """A photographed question.

    The image is transcribed to text and discarded; from that point this is the
    same call as send_message() and runs the same handle_student_message(). The
    tutoring model never sees the picture, so grounding, citations, refusals and
    validation behave exactly as they do for a typed question.

    Order of operations is the security-relevant part, and it is cheapest-first
    on purpose:
        auth -> feature flag -> rate limit -> size -> format -> moderation
        -> transcription -> existing pipeline
    Everything free happens before anything paid, and moderation happens before
    the image reaches any generation model.
    """
    if not PHOTO_QUESTIONS_ENABLED:
        # 404, not 501: an endpoint that is switched off should not advertise that
        # it exists and might be switched on.
        raise HTTPException(status_code=404, detail="Not found")
    if current.role != "student" or current.student_id is None:
        raise HTTPException(status_code=403, detail="Students only")
    if service.rate_limited(current.user_id):
        raise HTTPException(status_code=429, detail="Thoda dheere, dost! Ek minute ruk ke phir poochho.")

    # Read with a hard ceiling rather than trusting Content-Length, which is a
    # claim by the client. One byte over is enough to reject on.
    raw = photo.file.read(MAX_IMAGE_BYTES + 1)
    if len(raw) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="That photo is too large. Try taking it again.")

    try:
        vision.validate_image(raw, photo.content_type or "")
    except vision.ImageRejected as e:
        raise HTTPException(status_code=422, detail=str(e))

    student = profile.get_student(db, current.student_id)
    if student is None:
        raise HTTPException(status_code=404, detail="Student profile not found")

    try:
        if vision.moderate_image(raw, photo.content_type or ""):
            # The image is not stored, so the flag records that a photo was sent
            # and blocked, not its contents. A parent reviewing this sees an event
            # they can ask their child about, which is the point of the record.
            safety.record_flag(db, student.id, "inbound", "[photo question, blocked]")
            log.warning("photo flagged by moderation, student=%s", student.id)
            raise HTTPException(status_code=422, detail=FALLBACK_MODERATED)
        text_in, p_tok, c_tok = vision.transcribe(raw, photo.content_type or "")
    except vision.ImageRejected as e:
        raise HTTPException(status_code=422, detail=str(e))
    except llm.LLMUnavailable:
        raise HTTPException(status_code=503, detail="Abhi photo padhne mein dikkat ho rahi hai. Thodi der baad try karo.")
    finally:
        # Explicit, immediately, on every path including the failures above. The
        # bytes are not written anywhere else in this function; this just stops
        # them lingering in the frame while the tutoring call runs.
        raw = b""
        photo.file.close()

    llm.record_spend(db, p_tok, c_tok)

    if new_session:
        service.close_open_sessions(db, student.id, "web")
    try:
        turn, conv_id, regen = service.handle_student_message(
            db, student, "web", text_in,
            force_new=new_session, target_id=conversation_id, source="photo",
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if regen is not None:
        background_tasks.add_task(memory.regenerate, regen.student_id, regen.grade, regen.transcript)
    return SendMessageOut(
        conversation_id=conv_id, reply=turn.reply,
        grounding=turn.grounding, citation=turn.citation,
        source_excerpt=turn.source_excerpt, transcribed_text=text_in,
    )


@router.get("/conversations", response_model=list[ConversationOut], tags=["web-client"])
def list_conversations(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ConversationOut]:
    """Caller's own conversations only — scope comes from the token, no id accepted."""
    if current.role != "student" or current.student_id is None:
        raise HTTPException(status_code=403, detail="Students only")
    return service.list_conversations(db, current.student_id, limit, offset)


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
def get_messages(
    conversation_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MessageOut]:
    owner_student_id = service.conversation_owner(db, conversation_id)
    if owner_student_id is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    # Role AND ownership: students read only their own transcripts. Parents get
    # summaries via /students/{id}/summary — never raw transcripts.
    if current.role != "student" or current.student_id != owner_student_id:
        raise HTTPException(status_code=403, detail="Not your conversation")
    return service.list_messages(db, conversation_id)


@router.delete("/conversations/{conversation_id}", status_code=204, tags=["web-client"])
def delete_conversation(
    conversation_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Remove a session from the student's list.

    SOFT delete. The row and its messages stay: `moderation_flags` and the transcript
    behind it are the evidence for the parent-review promise, and a child who has just
    asked something they regret must not be able to erase it by tapping a bin icon.

    Ownership is enforced inside hide_conversation() by SQL predicate, not by a role
    check here — a role check alone is the IDOR shape.
    """
    if current.role != "student" or current.student_id is None:
        raise HTTPException(status_code=403, detail="Students only")
    if not service.hide_conversation(db, current.student_id, conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
