import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class MessageOut(BaseModel):
    id: uuid.UUID
    sender: str
    content: str
    created_at: datetime
    # Provenance, so a reloaded transcript shows the same chapter attribution the
    # live turn did. NULL on student messages.
    grounding: str | None = None
    citation: str | None = None

    model_config = {"from_attributes": True}


class SendMessageIn(BaseModel):
    text: str
    #   new_session      the student tapped "New chat" — close what is open, start clean
    #   conversation_id  the student reopened one from History and wants to continue it
    # Ownership of conversation_id is verified in the service against student_id, never
    # trusted from here — looking it up without that predicate would be an IDOR.
    new_session: bool = False
    conversation_id: uuid.UUID | None = None
    # The class and subject for THIS chat. Applied only when a conversation is
    # created, ignored on an existing one, because changing either mid-thread would
    # re-answer earlier turns from a different textbook. NULL grade falls back to the
    # student's profile; NULL subject searches every subject.
    grade: int | None = Field(default=None, ge=5, le=10)
    subject: str | None = Field(default=None, max_length=32)

    model_config = {"str_strip_whitespace": True}


class SendMessageOut(BaseModel):
    conversation_id: uuid.UUID
    reply: str
    # System state as a field, so the client never infers it from the reply text.
    #   grounded    answered from the textbook; `citation` names the chapter
    #   weak        loosely related material; the reply says so itself
    #   empty       nothing found; the reply says it is not in their book
    #   not_needed  greeting / language request — no search was run
    #   blocked     moderation, validation, spend cap, or provider outage
    grounding: str = "n/a"
    citation: str | None = None
    # The passage the answer was built from — the difference between asking a child to
    # trust a chapter label and letting them read the sentence. Truncated server-side:
    # shipping 5.6kB of chunk text per turn to a budget phone is not free.
    source_excerpt: str | None = None


class ConversationOut(BaseModel):
    id: uuid.UUID
    channel: str
    started_at: datetime
    last_message_at: datetime | None
    # The student's own first message, used as the History label.
    title: str | None = None
    closed_at: datetime | None = None
    # NULL means the conversation follows the student's profile grade.
    grade: int | None = None
    subject: str | None = None

    model_config = {"from_attributes": True}
