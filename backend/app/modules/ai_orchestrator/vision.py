"""Photo questions: read the question out of an image, then stop.

THE ONE RULE THIS MODULE EXISTS TO ENFORCE
The image is an INPUT METHOD, not an answer source. This module turns a photo
into a line of text and hands that text to the existing pipeline. The image
never reaches the tutoring model, and the tutoring model's context is exactly
what it would have been had the student typed the question.

That constraint is what keeps every downstream guarantee intact. Retrieval,
grounding states, citations, the not-in-your-textbook refusal, output moderation
and response validation all continue to operate on text and are unchanged by this
feature. Answering directly from the image would have bypassed all of them, and
"GuruJi read it off your homework sheet" is precisely the ungrounded answer the
product exists not to give.

WHAT IS NOT STORED
The image is held in memory for the duration of one request and discarded. It is
never written to disk, never written to Postgres, never sent to an object store.
What persists is the transcribed text, marked `source='photo'` so a founder or
parent reviewing a transcript can see the question arrived as a picture.

That is a deliberate DPDP position: a photograph taken by a child is personal
data of both the child and anyone else in frame, and the lawful basis for
retaining it does not exist. Not retaining it means there is nothing to retain
a basis for. The same reasoning is already recorded against `students.avatar`
in schema.sql.
"""
import base64
import logging

from app.config import MAX_IMAGE_BYTES, VISION_MODEL
from app.modules.ai_orchestrator import llm

log = logging.getLogger("guruji.vision")

# JPEG, PNG and WebP only. The client downscales and re-encodes to JPEG before
# upload, so anything else here is either a bug or a hand-crafted request.
ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}

# Magic bytes, checked against the declared MIME type. A client-declared
# Content-Type is a claim, not evidence, and the file is about to be handed to a
# third-party API — cheap to verify, and it rejects the "rename a .zip to .jpg"
# shape of request before it costs anything.
_MAGIC = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/webp": (b"RIFF",),
}

TRANSCRIBE_PROMPT = """You read school homework photos for an Indian student.

Write out EVERY question visible in this image as plain text, exactly as asked.
Include any numbers, units and labels that are part of each question.

Rules:
- Transcribe ONLY. Never answer, never explain, never add a hint.
- Transcribe ALL the questions you can read, not just the first. A student
  photographs a whole worksheet; dropping the rest silently loses work they
  wanted help with.
- Keep the numbering the page uses. If it has none, number them 1., 2., 3.
- Put each question on its own line.
- If the page shows worked solutions or answers below the questions, transcribe
  the QUESTIONS only. Never copy an answer across.
- Preserve the language it is written in. Do not translate.
- If the image contains no readable question - it is a photo of a person, a
  place, a blank page, or is too blurry to read - reply with exactly:
  NO_QUESTION_FOUND

Output the question text alone, with no preamble and no quotation marks."""

NO_QUESTION = "NO_QUESTION_FOUND"

# Enough for a full worksheet of questions. Raised from 300 when transcription
# went from one question to all of them: a page of six with sub-parts overflowed
# the old cap and came back truncated mid-question.
#
# Still under the 500 a tutoring reply gets. The signal that the model
# started ANSWERING rather than reading is no longer length alone — it is the
# explicit instruction above plus the character guard in transcribe().
TRANSCRIBE_MAX_TOKENS = 450


class ImageRejected(Exception):
    """Raised for anything wrong with the upload itself, before any paid call."""


def validate_image(raw: bytes, mime: str) -> None:
    """Reject bad uploads before spending money on them.

    Ordered cheapest-check-first on purpose: size and type are free, the
    moderation and vision calls are not.
    """
    if mime not in ALLOWED_MIME:
        raise ImageRejected("Only JPEG, PNG or WebP photos are supported.")
    if not raw:
        raise ImageRejected("That photo was empty.")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ImageRejected("That photo is too large. Try taking it again.")
    if not any(raw.startswith(sig) for sig in _MAGIC[mime]):
        raise ImageRejected("That file is not the image type it claims to be.")


def _data_url(raw: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def moderate_image(raw: bytes, mime: str) -> bool:
    """True = flagged. BLOCKING, exactly as text moderation is.

    A camera pointed at a homework page also catches faces, rooms and siblings,
    and the child holding it is a minor. Text moderation cannot see any of that,
    so an unmoderated image path would be a hole straight through the safety
    layer rather than an addition to it.
    """
    return llm.moderate_image_url(_data_url(raw, mime))


def transcribe(raw: bytes, mime: str) -> tuple[str, int, int]:
    """Return (question_text, prompt_tokens, completion_tokens).

    Raises ImageRejected when nothing readable is in the picture, so the student
    gets "I could not read that" rather than the pipeline retrieving against an
    empty string and refusing for the wrong reason.
    """
    text, p_tok, c_tok = llm.chat_vision(
        model=VISION_MODEL,
        system=TRANSCRIBE_PROMPT,
        image_url=_data_url(raw, mime),
        max_tokens=TRANSCRIBE_MAX_TOKENS,
    )
    text = text.strip().strip('"').strip()

    if not text or NO_QUESTION in text.upper():
        raise ImageRejected(
            "Main is photo mein sawaal padh nahi paaya. Thodi roshni mein, "
            "seedha upar se photo lo — ya sawaal type kar do."
        )

    # A transcription materially longer than the cap suggests the model started
    # answering rather than reading. Truncating is safe: downstream this is only
    # ever used as a search query and as the stored student message.
    if len(text) > 2000:
        text = text[:2000].rstrip()

    log.info("photo transcribed to %d chars", len(text))
    return text, p_tok, c_tok
