import uuid

from sqlalchemy import CheckConstraint, Column, DateTime, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Student(Base):
    __tablename__ = "students"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), unique=True, nullable=False)
    grade = Column(Integer, nullable=False)
    board = Column(Text, nullable=False, default="NCERT")
    preferred_language = Column(Text, default="hinglish")
    # What the child calls themselves and the glyph they picked. Both nullable; the
    # UI falls back rather than inventing a name.
    display_name = Column(Text)
    avatar = Column(Text)
    onboarded_at = Column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint("grade BETWEEN 5 AND 10"),)


class ParentLink(Base):
    __tablename__ = "parent_links"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parent_user_id = Column(UUID(as_uuid=True), nullable=False)
    student_id = Column(UUID(as_uuid=True), nullable=False)
    verified_at = Column(DateTime(timezone=True))
