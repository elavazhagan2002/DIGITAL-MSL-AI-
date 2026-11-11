"""
Database Models for Standalone Research Document Chat
Includes User and Chat History (ChatSession, ChatMessage).
Uses SQLite or PostgreSQL via SQLAlchemy.
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean, Index
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.sql import func
import uuid

Base = declarative_base()


class User(Base):
    """User representation for access control and ownership."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()), index=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=True)
    role = Column(String(50), default="contributor")  # contributor, super_admin, etc.
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "username": self.username,
            "email": self.email,
            "role": self.role,
        }


class ChatSession(Base):
    """Chat Session model for storing conversation sessions scoped to user and repository."""
    __tablename__ = "chat_sessions"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(100), nullable=False, unique=True, index=True)
    user_id = Column(String(36), nullable=False, index=True)
    repository_id = Column(String(100), nullable=True, index=True)
    title = Column(String(500), nullable=True)
    active_agent = Column(String(100), default="general_chat")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationship to messages
    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan", order_by="ChatMessage.created_at")

    __table_args__ = (
        Index('idx_chat_session_user', 'user_id', 'is_active'),
        Index('idx_chat_session_repo', 'repository_id'),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "repository_id": self.repository_id,
            "title": self.title,
            "active_agent": self.active_agent,
            "is_active": self.is_active,
            "message_count": len(self.messages) if self.messages else 0,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ChatMessage(Base):
    """Chat Message model for storing individual question/answer messages."""
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("chat_sessions.id"), nullable=False, index=True)
    role = Column(String(20), nullable=False)  # 'user' or 'assistant'
    content = Column(Text, nullable=False)
    agent_type = Column(String(100), default="general_chat")
    message_metadata = Column(Text, nullable=True)  # JSON string for citations, tokens, stats
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    session = relationship("ChatSession", back_populates="messages")

    def to_dict(self):
        return {
            "id": self.id,
            "session_id": self.session_id,
            "role": self.role,
            "content": self.content,
            "agent_type": self.agent_type,
            "metadata": self.message_metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
