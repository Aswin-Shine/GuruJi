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
from app.db.session import SessionLocal, get_db
from app.modules.conversation import service
from app.modules.conversation.schemas import ConversationOut, MessageOut, SendMessageIn, SendMessageOut
from app.modules.identity import service as identity
from app.modules.ai_orchestrator import llm, vision
from app.modules.ai_orchestrator.orchestrator import FALLBACK_MODERATED, FALLBACK_UNAVAILABLE
from app.modules.whatsapp import service as whatsapp
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


def _extract_messages(payload: dict) -> list[tuple[str, str, str | None]]:
    """Every text message in the payload as (phone, text, whatsapp_message_id).

    Meta may batch several messages across entries/changes into one POST, so all of
    them are walked — reading only the first would drop the rest silently. Status
    events and non-text messages are skipped. A missing message id is NOT a
    rejection — dedupe is simply skipped for that message."""
    out = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            for msg in (change.get("value") or {}).get("messages") or []:
                try:
                    if msg.get("type") == "text":
                        out.append((msg["from"], msg["text"]["body"], msg.get("id")))
                except (KeyError, TypeError, AttributeError):
                    continue
    return out


def _claim_message(db: Session, message_id: str) -> bool:
    """Claim a WhatsApp message id. True = ours to process, False = duplicate.

    Meta delivers at-least-once. The webhook now acks inside Meta's ~3-5s window and
    processes afterwards, so retries should be rare — but a lost 200 (network blip,
    container restart mid-response) still produces one, and without this claim that
    retry buys a second billed LLM call, a duplicate row in `messages`, and a
    duplicate reply on the student's phone."""
    inserted = db.execute(
        text("INSERT INTO processed_webhook_messages (whatsapp_message_id) VALUES (:i) ON CONFLICT DO NOTHING"),
        {"i": message_id},
    ).rowcount
    db.commit()
    return inserted == 1


def _release_message(db: Session, message_id: str) -> None:
    """Undo the claim when processing raises.

    The claim table is a record of messages actually processed, so a crashed one
    must not sit in it. Since the ack has already gone out by the time processing
    runs, Meta will not normally retry — the student is told to try again instead
    (see _process_inbound_task) — but if Meta never received our 200 its retry must
    be free to reprocess rather than be deduped into silence."""
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
    """Inbound WhatsApp message: verify, claim, ack, and hand off.

    The response is an ACK, not the answer. Meta retries anything slower than its
    ~3-5s window and a tutoring turn is routinely 4-8s (retrieval p50 alone is
    3.7s), so the pipeline runs AFTER this returns, as a background task, and the
    reply reaches the student through the Cloud API rather than this body.

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
    if not isinstance(payload, dict):
        return {"status": "ignored"}
    messages = _extract_messages(payload)
    if not messages:
        return {"status": "ignored"}

    accepted = 0
    for phone, text_in, message_id in messages:
        # Gates everything below: a duplicate must never reach the point where a
        # second LLM call is billed or a second reply is sent.
        if message_id is None:
            log.warning("webhook message missing id, dedupe skipped")
        elif not _claim_message(db, message_id):
            continue
        # BackgroundTasks run in order, so a student's batched messages are
        # answered in the order they were sent.
        background_tasks.add_task(_process_inbound_task, phone, text_in, message_id)
        accepted += 1
    return {"status": "accepted" if accepted else "duplicate", "accepted": accepted}


def _process_inbound_task(phone: str, text_in: str, message_id: str | None) -> None:
    """The whole turn, after the ack. Runs on the thread pool once the 200 is sent.

    Opens its OWN session: get_db() has closed the request's by the time a
    background task runs. Never raises — an exception here would surface only in
    the server log after the response, so it is handled in full: the claim is
    released, the student gets the honest network-slow fallback, and the memory
    regeneration is simply skipped."""
    db = SessionLocal()
    try:
        status, reply, regen = _process_inbound(db, phone, text_in)
    except Exception:
        log.exception("webhook processing failed phone=%s", phone)
        if message_id is not None:
            _release_message(db, message_id)
        whatsapp.send_text(phone, FALLBACK_UNAVAILABLE)
        return
    finally:
        db.close()

    # Outside the try above: once the turn is persisted, nothing after it may send
    # the failure fallback or release the claim — the student already has an answer.
    if reply is not None:
        whatsapp.send_text(phone, reply)  # never raises
    log.info("webhook_processed phone=%s status=%s", phone, status)
    if regen is not None:
        # After the send, so summarisation never sits between the student and
        # their answer.
        try:
            memory.regenerate(regen.student_id, regen.grade, regen.transcript)
        except Exception:
            log.exception("memory regeneration failed student_id=%s", regen.student_id)


def _process_inbound(db: Session, phone: str, text_in: str) -> tuple[str, str | None, service.RegenArgs | None]:
    """One inbound turn. Returns (status, reply_or_None, regen_args_or_None).

    Every branch returns a reply for the caller to deliver — onboarding prompts and
    refusals included — because on WhatsApp there is no other channel to say it on.
    Nothing here raises for a product reason; a raise means a genuine fault."""
    # Same length ceiling send_message() enforces.
    if not text_in.strip() or len(text_in) > MAX_INBOUND_CHARS:
        return "rejected", TOO_LONG, None

    # Checked BEFORE get_or_create_user, which provisions an account on first contact,
    # so an unknown number must never reach it. Empty list = open.
    if ALLOWED_PHONE_NUMBERS and phone.lstrip("+") not in {p.lstrip("+") for p in ALLOWED_PHONE_NUMBERS}:
        log.warning("inbound from non-allowlisted number, dropped")
        return "not_allowed", NOT_INVITED, None

    user = identity.get_or_create_user(db, phone, "student")
    if user.role != "student":
        return "ignored", None, None
    if service.rate_limited(user.id):
        return "rate_limited", "Thoda dheere, dost! Ek minute ruk ke phir poochho. 😅", None

    student = profile.get_student_by_user(db, user.id)
    if student is None:
        # Deliberate simplification: onboarding collects ONLY the class, because grade
        # is the one field retrieval requires to function. A multi-question flow needs
        # per-user turn-state tracking, which is not built yet.
        stripped = text_in.strip()
        if stripped.isdigit() and 5 <= int(stripped) <= 10:
            student = profile.create_student(db, user.id, int(stripped), "NCERT", "hinglish")
            return "onboarded", ONBOARD_DONE.format(grade=student.grade), None
        return "onboarding", ONBOARD_ASK_GRADE, None

    turn, _, regen = service.handle_student_message(db, student, "whatsapp", text_in)
    log.info("outbound_reply phone=%s grounding=%s reply=%r", phone, turn.grounding, turn.reply)
    return "ok", turn.reply, regen


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
