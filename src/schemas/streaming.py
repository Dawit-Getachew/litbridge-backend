"""Schema for server-sent events emitted during streaming search and chat."""

from typing import Any, Literal

from pydantic import BaseModel

StreamEventType = Literal[
    "search_started",
    "status",
    "source_searching",
    "source_completed",
    "source_failed",
    "dedup_completed",
    "enrichment_update",
    "record_enriched",
    "thinking",
    "search_completed",
    "chat_started",
    "citation",
    "chat_completed",
    "error",
]


class StreamEvent(BaseModel):
    """SSE event sent during streaming search or chat.

    Examples:
        {"event": "search_started", "data": {"search_id": "...", "sources": [...]}}
        {"event": "status", "data": {"message": "Searching PubMed..."}}
        {"event": "source_searching", "data": {"source": "pubmed"}}
        {"event": "source_completed", "data": {"source": "pubmed", "count": 45}}
        {"event": "record_enriched", "data": {"id": "r1", "tldr": "..."}}
        {"event": "thinking", "data": {"chunk": "Based on these results..."}}
        {"event": "search_completed", "data": {"total_count": 150}}
        {"event": "chat_started", "data": {"conversation_id": "...", "resolved_records": [...]}}
        {"event": "chat_completed", "data": {"conversation_id": "...", "message_id": "..."}}
    """

    event: StreamEventType
    data: dict[str, Any]
