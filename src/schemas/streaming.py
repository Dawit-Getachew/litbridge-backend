"""Schema for server-sent events emitted during search."""

from typing import Any, Literal

from pydantic import BaseModel


class StreamEvent(BaseModel):
    """SSE event sent during streaming search.

    Examples:
        {"event": "search_started", "data": {"search_id": "...", "sources": [...]}}
        {"event": "source_completed", "data": {"source": "pubmed", "count": 45}}
        {"event": "dedup_completed", "data": {"total_before": 180, "total_after": 150}}
        {"event": "thinking", "data": {"chunk": "Based on these results..."}}
        {"event": "search_completed", "data": {"total_count": 150}}
    """

    event: Literal[
        "search_started",
        "source_completed",
        "source_failed",
        "dedup_completed",
        "enrichment_update",
        "thinking",
        "search_completed",
        "error",
    ]
    data: dict[str, Any]
