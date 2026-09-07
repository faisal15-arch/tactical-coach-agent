"""Persistent conversation memory backed by SQLAlchemy."""

from copy import deepcopy
from typing import Optional

from sqlalchemy.orm import Session

from core.models import ConversationMessage, ConversationSession


def load_session_context(db: Optional[Session], session_id: str) -> dict:
    """Return saved graph/live context, or an empty dict when unavailable."""
    if db is None:
        return {}

    try:
        session = db.get(ConversationSession, session_id)
        return deepcopy(session.context) if session and session.context else {}
    except Exception as exc:
        db.rollback()
        print(f"[memory] Could not load session {session_id}: {exc}")
        return {}


def get_recent_messages(
    db: Optional[Session],
    session_id: str,
    role: Optional[str] = None,
    limit: int = 20,
) -> list[ConversationMessage]:
    """Return recent messages in chronological order."""
    if db is None:
        return []

    try:
        query = db.query(ConversationMessage).filter(
            ConversationMessage.session_id == session_id
        )
        if role:
            query = query.filter(ConversationMessage.role == role)
        messages = query.order_by(ConversationMessage.id.desc()).limit(limit).all()
        return list(reversed(messages))
    except Exception as exc:
        db.rollback()
        print(f"[memory] Could not read messages for {session_id}: {exc}")
        return []


def save_conversation_turn(
    db: Optional[Session],
    session_id: str,
    match_id: Optional[int],
    question: str,
    answer: str,
    context: dict,
) -> bool:
    """Persist the latest context and a complete user/assistant turn."""
    if db is None:
        return False

    try:
        conversation = db.get(ConversationSession, session_id)
        if conversation is None:
            conversation = ConversationSession(
                session_id=session_id,
                match_id=match_id,
                context=deepcopy(context),
            )
            db.add(conversation)
            db.flush()
        else:
            conversation.match_id = match_id
            conversation.context = deepcopy(context)

        db.add_all([
            ConversationMessage(
                session_id=session_id,
                match_id=match_id,
                role="user",
                content=question,
            ),
            ConversationMessage(
                session_id=session_id,
                match_id=match_id,
                role="assistant",
                content=answer,
            ),
        ])
        db.commit()
        return True
    except Exception as exc:
        db.rollback()
        print(f"[memory] Could not save session {session_id}: {exc}")
        return False
