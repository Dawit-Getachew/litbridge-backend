"""Pydantic schemas for the conversational chat endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """User's chat message — fully natural language, no IDs required."""

    search_id: str
    message: str = Field(..., min_length=1, max_length=4000)
    conversation_id: str | None = None
    # Week-1 LitPortal merger (proposal §3.3.B): when the caller has explicitly
    # selected which citations should ground the answer, send them here. The
    # service skips natural-language reference resolution and uses these
    # records as the ground-truth context. Empty/null preserves the legacy
    # behavior (top-N + resolve_references).
    selected_record_ids: list[str] | None = None


class ResolvedRecord(BaseModel):
    """Compact reference to a record the system resolved from the message."""

    id: str
    title: str
    first_author: str | None = None
    year: int | None = None


class MessageResponse(BaseModel):
    """A single message in conversation history."""

    id: str
    role: str
    content: str
    record_ids: list[str] | None = None
    created_at: datetime


class ConversationResponse(BaseModel):
    """Metadata for a conversation thread."""

    id: str
    search_id: str
    title: str | None = None
    message_count: int
    created_at: datetime
    updated_at: datetime


class ConversationHistoryResponse(BaseModel):
    """Full conversation with messages."""

    conversation: ConversationResponse
    messages: list[MessageResponse]
