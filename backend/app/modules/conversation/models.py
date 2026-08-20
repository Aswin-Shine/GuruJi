import uuid

from sqlalchemy import CheckConstraint, Column, DateTime, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id = Column(UUID(as_uuid=True), nullable=False)
    channel = Column(Text, nullable=False)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    last_message_at = Column(DateTime(timezone=True))
    # closed_at ends a session ("New chat"). hidden_at removes it from the student's
    # list WITHOUT destroying the transcript the parent-review promise depends on.
    closed_at = Column(DateTime(timezone=True))
    hidden_at = Column(DateTime(timezone=True))
    title = Column(Text)
    grade = Column(Integer)    # NULL falls back to students.grade
    subject = Column(Text)     # NULL = any subject
    __table_args__ = (CheckConstraint("channel IN ('whatsapp','web')"),)


class Message(Base):
    __tablename__ = "messages"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(UUID(as_uuid=True), nullable=False)
    sender = Column(Text, nullable=False)
    content = Column(Text, nullable=False)
    tokens_used = Column(Integer)
    model_used = Column(Text)
    # How a STUDENT message arrived: NULL/'text' when typed, 'photo' when
    # transcribed from an image. The image is never stored, so this is the only
    # record that one existed.
    source = Column(Text)
    # Provenance, so it survives a reload. NULL on student messages.
    grounding = Column(Text)
    citation = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint("sender IN ('student','assistant')"),)
