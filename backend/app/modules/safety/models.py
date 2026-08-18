"""Owns moderation_flags only. A separate module because ai_orchestrator owns no
tables by design, and conversation.service already imports the orchestrator —
putting this there would be a circular import."""
from sqlalchemy import BigInteger, Column, DateTime, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class ModerationFlag(Base):
    __tablename__ = "moderation_flags"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    student_id = Column(UUID(as_uuid=True), nullable=False)
    direction = Column(Text, nullable=False)  # 'inbound' | 'outbound'
    content = Column(Text, nullable=False)
    flagged_at = Column(DateTime(timezone=True), server_default=func.now())
