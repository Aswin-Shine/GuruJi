import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class StudentCreate(BaseModel):
    grade: int = Field(ge=5, le=10)
    board: str = "NCERT"
    preferred_language: str = "hinglish"


class StudentPatch(BaseModel):
    grade: int | None = Field(default=None, ge=5, le=10)
    preferred_language: str | None = None
    # display_name is capped short: it is a label on a chat header, not a free-text
    # field, and an unbounded string from a minor is an unwatched moderation surface.
    display_name: str | None = Field(default=None, max_length=24)
    # A key into a fixed client-side glyph set, never a URL and never an upload:
    # no S3 bucket exists at Phase 1, no image moderation exists, and there is no
    # lawful basis under DPDP to store a photograph of a minor.
    avatar: str | None = Field(default=None, max_length=16)
    preferred_language: str | None = Field(default=None, max_length=16)


class StudentOut(BaseModel):
    id: uuid.UUID
    grade: int
    board: str
    preferred_language: str
    # Both nullable; the UI falls back to a neutral default rather than inventing a
    # name for a child.
    display_name: str | None = None
    avatar: str | None = None

    model_config = {"from_attributes": True}


class StudentSummaryOut(BaseModel):
    """Parent-facing summary. Deliberately NO raw transcripts."""
    student_id: uuid.UUID
    grade: int
    board: str
    total_messages: int
    struggle_topics: list[str]
    mastered_topics: list[str]


class LinkParentRequest(BaseModel):
    parent_phone_number: str = Field(min_length=8, max_length=20)


class LinkParentResponse(BaseModel):
    parent_user_id: uuid.UUID
    link_pin: str  # student relays this to the parent out-of-band (no SMS in Phase 1)
    verified: bool


class VerifyLinkRequest(BaseModel):
    link_pin: str = Field(min_length=6, max_length=6)


class FlaggedExchangeOut(BaseModel):
    """One exchange moderation refused, shown only to a VERIFIED parent."""
    id: int
    direction: str
    content: str
    flagged_at: datetime
