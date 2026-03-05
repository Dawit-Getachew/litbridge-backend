"""Helpers for formatting and streaming server-sent events."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator

from src.schemas.streaming import StreamEvent

KEEPALIVE_INTERVAL_SECONDS = 15
KEEPALIVE_COMMENT = ": keepalive\n\n"


def format_sse(event: StreamEvent) -> str:
    """Serialize one stream event into SSE wire format."""
    payload = json.dumps(event.data)
    return f"event: {event.event}\ndata: {payload}\n\n"


async def stream_generator(
    events: AsyncGenerator[StreamEvent, None],
) -> AsyncGenerator[str, None]:
    """Wrap event generator with SSE formatting, keepalive, and error handling.

    Emits a ``: keepalive`` SSE comment every 15 seconds of silence to prevent
    proxy and load-balancer idle-connection timeouts.
    """
    try:
        pending_event: asyncio.Task | None = None
        event_iter = events.__aiter__()

        while True:
            if pending_event is None:
                pending_event = asyncio.ensure_future(event_iter.__anext__())

            done, _ = await asyncio.wait(
                {pending_event},
                timeout=KEEPALIVE_INTERVAL_SECONDS,
            )

            if done:
                try:
                    event = pending_event.result()
                except StopAsyncIteration:
                    break
                pending_event = None
                yield format_sse(event)
            else:
                yield KEEPALIVE_COMMENT
    except Exception as exc:
        yield format_sse(StreamEvent(event="error", data={"error": str(exc)}))
