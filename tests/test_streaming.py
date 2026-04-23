"""API tests for SSE streaming search endpoint."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from src.core import deps
from src.schemas.enums import OAStatus, SourceType
from src.schemas.records import RawRecord, UnifiedRecord
from src.schemas.search import SearchRequest
from src.services.prisma_service import PrismaService
from src.services.streaming_search_service import StreamingSearchService


@dataclass
class FakeSearchSession:
    """In-memory session model used by streaming API tests."""

    id: UUID
    query: str
    query_type: str
    search_mode: str
    sources: list[str]
    pico: dict | None
    status: str = "processing"
    total_identified: int = 0
    total_after_dedup: int = 0
    results: list[dict] = field(default_factory=list)
    sources_completed: list[str] = field(default_factory=list)
    sources_failed: list[str] = field(default_factory=list)
    completed_at: datetime | None = None


class InMemorySearchRepository:
    """Minimal in-memory repository for streaming endpoint tests."""

    def __init__(self) -> None:
        self.sessions: dict[str, FakeSearchSession] = {}

    async def create_session(self, request: SearchRequest, *, user_id: object = None) -> FakeSearchSession:
        session = FakeSearchSession(
            id=uuid4(),
            query=request.query,
            query_type=request.query_type.value,
            search_mode=request.search_mode.value,
            sources=[source.value for source in (request.sources or [])],
            pico=request.pico.model_dump(mode="json") if request.pico else None,
        )
        self.sessions[str(session.id)] = session
        return session

    async def update_session(self, session: FakeSearchSession) -> None:
        self.sessions[str(session.id)] = session

    async def get_session(self, search_id: str) -> FakeSearchSession | None:
        try:
            _ = UUID(search_id)
        except (TypeError, ValueError):
            return None
        return self.sessions.get(search_id)

    async def store_results(self, search_id: str, records: list[UnifiedRecord]) -> None:
        session = await self.get_session(search_id)
        if session is None:
            return
        session.results = [record.model_dump(mode="json") for record in records]
        session.total_after_dedup = len(records)


class MockFetcherService:
    """Fetcher mock carrying only the HTTP client expected by the service."""

    def __init__(self) -> None:
        self.client = AsyncMock()


class MockDedupService:
    """Dedup mock that keeps one output record per raw input record."""

    def deduplicate(
        self,
        records: list[RawRecord],
        query: str | None = None,  # noqa: ARG002
        query_type: object = None,  # noqa: ARG002
    ) -> list[UnifiedRecord]:
        return [
            UnifiedRecord(
                id=f"rec-{index}",
                title=record.title,
                authors=record.authors,
                source=record.source,
                sources_found_in=[record.source],
                oa_status=record.oa_status,
                abstract=record.abstract,
            )
            for index, record in enumerate(records)
        ]


class FakeSourceRepository:
    """Per-source repository mock used by streaming service tests."""

    def __init__(
        self,
        source: SourceType,
        source_counts: dict[SourceType, int],
        failing_sources: set[SourceType],
    ) -> None:
        self.source = source
        self.source_counts = source_counts
        self.failing_sources = failing_sources

    async def search(
        self,
        query: str,
        max_results: int = 100,
        sort_mode: str = "relevance",  # noqa: ARG002
    ) -> list[RawRecord]:
        await asyncio.sleep(0.001)
        if self.source in self.failing_sources:
            raise TimeoutError("timeout")

        count = min(self.source_counts.get(self.source, 1), max_results)
        return [
            RawRecord(
                source_id=f"{self.source.value}-{index}",
                source=self.source,
                title=f"{self.source.value} study {index} {query}",
                authors=["Author One"],
                oa_status=OAStatus.UNKNOWN,
            )
            for index in range(count)
        ]


@dataclass
class StreamingApiContext:
    """Mutable context shared between fixture and tests."""

    client: AsyncClient
    failing_sources: set[SourceType]


async def _consume_sse(response) -> tuple[list[dict], list[str]]:
    events: list[dict] = []
    lines: list[str] = []
    current_event: str | None = None
    current_data_lines: list[str] = []

    async for line in response.aiter_lines():
        lines.append(line)
        if line == "":
            if current_event is not None:
                payload = "\n".join(current_data_lines) if current_data_lines else "{}"
                events.append({"event": current_event, "data": json.loads(payload)})
            current_event = None
            current_data_lines = []
            continue

        if line.startswith("event:"):
            current_event = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            current_data_lines.append(line.removeprefix("data:").strip())

    if current_event is not None:
        payload = "\n".join(current_data_lines) if current_data_lines else "{}"
        events.append({"event": current_event, "data": json.loads(payload)})

    return events, lines


@pytest.fixture
def streaming_api_context(async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> StreamingApiContext:
    """Override streaming search dependency with deterministic in-memory collaborators."""
    from src.main import app
    from src.services import streaming_search_service as streaming_module

    source_counts = {
        SourceType.PUBMED: 2,
        SourceType.OPENALEX: 1,
        SourceType.EUROPEPMC: 1,
        SourceType.CLINICALTRIALS: 1,
    }
    failing_sources: set[SourceType] = set()

    async def fake_translate_for_all_sources(*, query, query_type, pico=None, sources=None, **_kwargs):
        selected_sources = sources or list(SourceType)
        return {source: query for source in selected_sources}

    def fake_get_repository(source: SourceType, client) -> FakeSourceRepository:
        _ = client
        return FakeSourceRepository(
            source=source,
            source_counts=source_counts,
            failing_sources=failing_sources,
        )

    monkeypatch.setattr(streaming_module, "translate_for_all_sources", fake_translate_for_all_sources)
    monkeypatch.setattr(streaming_module, "get_repository", fake_get_repository)

    service = StreamingSearchService(
        fetcher=MockFetcherService(),  # type: ignore[arg-type]
        dedup=MockDedupService(),  # type: ignore[arg-type]
        prisma=PrismaService(),
        search_repo=InMemorySearchRepository(),  # type: ignore[arg-type]
        redis_client=AsyncMock(),
        enrichment_service=AsyncMock(),
        oa_service=AsyncMock(),
    )

    app.dependency_overrides[deps.get_streaming_search_service] = lambda: service
    yield StreamingApiContext(client=async_client, failing_sources=failing_sources)
    app.dependency_overrides.pop(deps.get_streaming_search_service, None)


@pytest.mark.asyncio
async def test_post_search_stream_returns_sse_content_type(streaming_api_context: StreamingApiContext) -> None:
    async with streaming_api_context.client.stream(
        "POST",
        "/api/v1/search/stream",
        json={"query": "metformin cardiovascular", "search_mode": "quick"},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")


@pytest.mark.asyncio
async def test_stream_search_started_is_first_event(streaming_api_context: StreamingApiContext) -> None:
    async with streaming_api_context.client.stream(
        "POST",
        "/api/v1/search/stream",
        json={"query": "metformin cardiovascular", "search_mode": "quick"},
    ) as response:
        events, _lines = await _consume_sse(response)

    assert events
    assert events[0]["event"] == "search_started"


@pytest.mark.asyncio
async def test_stream_emits_source_completed_events(streaming_api_context: StreamingApiContext) -> None:
    async with streaming_api_context.client.stream(
        "POST",
        "/api/v1/search/stream",
        json={"query": "metformin cardiovascular", "search_mode": "quick"},
    ) as response:
        events, _lines = await _consume_sse(response)

    completed_events = [event for event in events if event["event"] == "source_completed"]
    assert completed_events
    assert len(completed_events) == len(SourceType)


@pytest.mark.asyncio
async def test_stream_emits_dedup_completed_event(streaming_api_context: StreamingApiContext) -> None:
    async with streaming_api_context.client.stream(
        "POST",
        "/api/v1/search/stream",
        json={"query": "metformin cardiovascular", "search_mode": "quick"},
    ) as response:
        events, _lines = await _consume_sse(response)

    dedup_events = [event for event in events if event["event"] == "dedup_completed"]
    assert len(dedup_events) == 1
    assert "total_before" in dedup_events[0]["data"]
    assert "total_after" in dedup_events[0]["data"]
    assert "duplicates_removed" in dedup_events[0]["data"]


@pytest.mark.asyncio
async def test_stream_search_completed_is_final_event(streaming_api_context: StreamingApiContext) -> None:
    async with streaming_api_context.client.stream(
        "POST",
        "/api/v1/search/stream",
        json={"query": "metformin cardiovascular", "search_mode": "quick"},
    ) as response:
        events, _lines = await _consume_sse(response)

    assert events
    assert events[-1]["event"] == "search_completed"


@pytest.mark.asyncio
async def test_stream_emits_source_failed_event_for_failed_source(
    streaming_api_context: StreamingApiContext,
) -> None:
    streaming_api_context.failing_sources.add(SourceType.OPENALEX)

    async with streaming_api_context.client.stream(
        "POST",
        "/api/v1/search/stream",
        json={"query": "metformin cardiovascular", "search_mode": "quick"},
    ) as response:
        events, _lines = await _consume_sse(response)

    failed_events = [event for event in events if event["event"] == "source_failed"]
    assert failed_events
    assert any(event["data"].get("source") == SourceType.OPENALEX.value for event in failed_events)


@pytest.mark.asyncio
async def test_stream_format_lines_are_event_or_data(streaming_api_context: StreamingApiContext) -> None:
    async with streaming_api_context.client.stream(
        "POST",
        "/api/v1/search/stream",
        json={"query": "metformin cardiovascular", "search_mode": "quick"},
    ) as response:
        _events, lines = await _consume_sse(response)

    for line in lines:
        if line == "":
            continue
        assert line.startswith("event:") or line.startswith("data:") or line.startswith(":")
