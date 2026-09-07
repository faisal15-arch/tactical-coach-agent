"""
Database models.

QueryLog stores every question a coach asks and what the agent decided —
this is your explainability audit trail: you can look back days later and
see exactly what data and confidence led to a recommendation.
"""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.sql import func

from core.db import Base


class QueryLog(Base):
    __tablename__ = "query_logs"

    id = Column(Integer, primary_key=True, index=True)
    question = Column(String, nullable=False)
    intent = Column(String, nullable=True)
    answer = Column(String, nullable=True)

    pressure = Column(Integer, nullable=True)
    bowler_scores = Column(JSON, nullable=True)
    strategies = Column(JSON, nullable=True)
    recommendation = Column(String, nullable=True)
    confidence = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ConversationSession(Base):
    """Durable state for one chat, independent of the Redis cache."""

    __tablename__ = "conversation_sessions"

    session_id = Column(String(64), primary_key=True)
    match_id = Column(Integer, nullable=True, index=True)
    context = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class ConversationMessage(Base):
    """A user or assistant message belonging to a durable chat session."""

    __tablename__ = "conversation_messages"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(
        String(64),
        ForeignKey("conversation_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    match_id = Column(Integer, nullable=True, index=True)
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PlayerTypeStatsCache(Base):
    """Persistent copy of slow external All-T20 bowling-type splits."""

    __tablename__ = "player_type_stats_cache"

    query_key = Column(String(200), primary_key=True)
    player_name = Column(String(200), nullable=False, index=True)
    stats = Column(JSON, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class StudentAccount(Base):
    """An administrator-provisioned student allowed to use the console."""

    __tablename__ = "student_accounts"

    student_id = Column(String(80), primary_key=True)
    full_name = Column(String(200), nullable=False)
    password_hash = Column(String(300), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class StudentAuthSession(Base):
    """Server-side login session; only a SHA-256 token digest is stored."""

    __tablename__ = "student_auth_sessions"

    token_hash = Column(String(64), primary_key=True)
    student_id = Column(
        String(80),
        ForeignKey("student_accounts.student_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
