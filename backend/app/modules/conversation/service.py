import collections
import time as time_mod
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import CONVERSATION_GAP_HOURS, RATE_LIMIT_PER_MIN
from app.modules.ai_orchestrator.orchestrator import TurnResult, orchestrate
from typing import TYPE_CHECKING

from app.modules.conversation.models import Conversation, Message

if TYPE_CHECKING:  # type-only: no runtime cross-module model import
    from app.modules.student_profile.models import Student

# In-process rate limiter. Fine for one container; needs Redis only if this ever
# runs multi-instance.
_rate_buckets: dict[uuid.UUID, collections.deque] = collections.defaultdict(collections.deque)


def rate_limited(user_id: uuid.UUID) -> bool:
    now = time_mod.monotonic()
    bucket = _rate_buckets[user_id]
    while bucket and now - bucket[0] > 60:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT_PER_MIN:
        return True
    bucket.append(now)
    return False


def get_or_create_conversation(
    db: Session,
    student_id: uuid.UUID,
    channel: str,
    *,
    force_new: bool = False,
    target_id: uuid.UUID | None = None,
    grade: int | None = None,
    subject: str | None = None,
) -> tuple[Conversation, bool]:
    """4-hour boundary rule, with two explicit overrides. Returns (conversation, is_new).

    The 4-hour rule alone gives the student no way to say "this is a new topic" or
    "carry on with the one from yesterday", so:

      force_new   the student tapped New chat. Closes the live session first so the
                  transcript window does not bleed a finished topic into a fresh one.
      target_id   the student reopened a specific conversation from History. Ownership
                  is checked here, not by the caller: a conversation_id arriving from a
                  client is untrusted input, and looking it up without the student_id
                  predicate would be an IDOR letting any student read any transcript.
    """
    if target_id is not None:
        conv = db.execute(
            select(Conversation).where(
                Conversation.id == target_id,
                Conversation.student_id == student_id,   # the ownership predicate
                Conversation.hidden_at.is_(None),
            )
        ).scalar_one_or_none()
        if conv is None:
            raise LookupError("conversation not found for this student")
        # Reopening a closed session revives it rather than forking a new one.
        conv.closed_at = None
        db.commit()
        return conv, False

    if not force_new:
        latest = db.execute(
            select(Conversation)
            .where(
                Conversation.student_id == student_id,
                Conversation.channel == channel,
                Conversation.closed_at.is_(None),
                Conversation.hidden_at.is_(None),
            )
            .order_by(Conversation.started_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        cutoff = datetime.now(timezone.utc) - timedelta(hours=CONVERSATION_GAP_HOURS)
        if latest is not None and latest.last_message_at is not None and latest.last_message_at > cutoff:
            return latest, False

    # The class is stamped at creation and never changes afterwards. Changing it
    # mid-conversation would silently re-answer earlier turns from a different book.
    conv = Conversation(student_id=student_id, channel=channel, grade=grade, subject=subject)
    db.add(conv)
    db.commit()
    return conv, True


def close_open_sessions(db: Session, student_id: uuid.UUID, channel: str) -> None:
    """Mark every open session closed. Called when the student starts a fresh chat."""
    now = datetime.now(timezone.utc)
    for conv in db.execute(
        select(Conversation).where(
            Conversation.student_id == student_id,
            Conversation.channel == channel,
            Conversation.closed_at.is_(None),
        )
    ).scalars():
        conv.closed_at = now
    db.commit()


def hide_conversation(db: Session, student_id: uuid.UUID, conversation_id: uuid.UUID) -> bool:
    """Soft-delete. Returns False if the id is not this student's.

    Deliberately NOT a DELETE. Messages are the evidence behind the parent-review
    promise; a child tapping a bin icon must not be able to erase a flagged exchange.
    Hidden from the student, intact for the parent.
    """
    conv = db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.student_id == student_id,
        )
    ).scalar_one_or_none()
    if conv is None:
        return False
    conv.hidden_at = datetime.now(timezone.utc)
    db.commit()
    return True


# 8 messages covers a full 3-step pedagogy ladder plus a turn of slack.
HISTORY_TURNS = 8
HISTORY_MAX_CHARS = 4000  # ~1000 tokens


def _recent_transcript(
    db: Session, conversation_id: uuid.UUID, limit: int = 20, max_chars: int = 6000
) -> str:
    """Recent transcript, capped by message count AND characters.

    The message cap alone bounds nothing useful: 8 messages at the 2000-char inbound
    ceiling is ~4k tokens, the entire context budget spent on history. Truncation
    keeps the tail, since recent turns matter most."""
    rows = db.execute(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at.desc()).limit(limit)
    ).scalars().all()
    joined = "\n".join(f"{m.sender}: {m.content}" for m in reversed(rows))
    return joined[-max_chars:]


@dataclass
class RegenArgs:
    """What the caller needs to schedule memory regeneration off the request path.

    A plain dataclass on purpose: BackgroundTasks is a FastAPI concern and does not
    belong in a service module."""
    student_id: uuid.UUID
    grade: int
    transcript: str


def handle_student_message(
    db: Session,
    student: "Student",
    channel: str,
    text_in: str,
    *,
    force_new: bool = False,
    target_id: uuid.UUID | None = None,
    grade: int | None = None,
    subject: str | None = None,
    source: str | None = None,
) -> tuple[TurnResult, uuid.UUID, RegenArgs | None]:
    """Returns (turn, conversation_id, regen_args_or_None). The caller schedules
    regeneration; this function never blocks on it.

    Returns the whole TurnResult rather than just the reply string, so the web route
    can hand the client real provenance instead of the client inferring it by
    string-matching the fallback constants."""
    conv, is_new = get_or_create_conversation(
        db, student.id, channel, force_new=force_new, target_id=target_id,
        grade=grade, subject=subject,
    )

    # Session ended (new conversation started) → memory should be regenerated from the
    # previous one. Prepared here, scheduled by the router AFTER the response, so the
    # ~1.5-4s summarisation call never lands on a message already close to Meta's ack
    # window. Consequence: the first turn of a new session reads the previous summary,
    # and the refresh lands before turn 2.
    regen: RegenArgs | None = None
    if is_new:
        prev = db.execute(
            select(Conversation)
            .where(Conversation.student_id == student.id, Conversation.id != conv.id)
            .order_by(Conversation.started_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if prev is not None:
            regen = RegenArgs(student.id, student.grade, _recent_transcript(db, prev.id))

    history = _recent_transcript(db, conv.id, limit=HISTORY_TURNS, max_chars=HISTORY_MAX_CHARS)
    db.add(Message(conversation_id=conv.id, sender="student", content=text_in, source=source))
    # The first thing the student typed is the most honest label available without
    # paying a model call to summarise it.
    if conv.title is None:
        conv.title = text_in.strip()[:60]
    db.commit()

    # The conversation's class wins over the profile's. NULL on rows created before
    # the column existed, which is why this is a fallback rather than a NOT NULL.
    effective_grade = conv.grade or student.grade
    turn = orchestrate(
        db, student.id, effective_grade, student.board, text_in, history, conv.subject
    )

    db.add(Message(
        conversation_id=conv.id, sender="assistant", content=turn.reply,
        tokens_used=turn.tokens, model_used=turn.model_used,
        grounding=turn.grounding, citation=turn.citation,
    ))
    conv.last_message_at = datetime.now(timezone.utc)
    db.commit()
    # Returns the conversation id too so the web endpoint can hand a client
    # something to fetch history with (webhook ignores it).
    return turn, conv.id, regen


def list_conversations(db: Session, student_id: uuid.UUID, limit: int, offset: int) -> list[Conversation]:
    """Caller's own conversations, newest activity first. Paginated — a long
    WhatsApp history must not force a web client to load everything at once."""
    return list(
        db.execute(
            select(Conversation)
            .where(
                Conversation.student_id == student_id,
                # Hidden rows are soft-deleted, so they must be excluded here as
                # well as at read time, or the bin icon appears to do nothing.
                Conversation.hidden_at.is_(None),
            )
            .order_by(Conversation.last_message_at.desc().nulls_last())
            .limit(limit)
            .offset(offset)
        ).scalars()
    )


def conversation_owner(db: Session, conversation_id: uuid.UUID) -> uuid.UUID | None:
    conv = db.get(Conversation, conversation_id)
    return conv.student_id if conv else None


def list_messages(db: Session, conversation_id: uuid.UUID) -> list[Message]:
    return list(
        db.execute(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at)).scalars()
    )
