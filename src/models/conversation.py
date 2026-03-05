"""Conversation and message ORM models for chat follow-up."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base


class Conversation(Base):
    """A chat conversation thread tied to a search session."""

    __tablename__ = "conversations"

    search_session_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("search_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    messages: Mapped[list[Message]] = relationship(
        "Message",
        back_populates="conversation",
        order_by="Message.created_at",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class Message(Base):
    """A single message within a conversation."""

    __tablename__ = "messages"

    conversation_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    record_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    conversation: Mapped[Conversation] = relationship(
        "Conversation",
        back_populates="messages",
    )


Index(
    "ix_conversations_search_session_id_created",
    Conversation.search_session_id,
    Conversation.created_at.desc(),
)

Index(
    "ix_messages_conversation_id_created",
    Message.conversation_id,
    Message.created_at,
)
