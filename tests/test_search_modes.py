"""Tests for search mode behavior across sync and streaming flows."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4
from unittest.mock import AsyncMock

import pytest

from src.schemas.enrichment import EnrichmentResponse
from src.schemas.enums import OAStatus, QueryType, SearchMode, SourceType
from src.schemas.records import RawRecord, UnifiedRecord
from src.schemas.search import SearchRequest
from src.services.fetcher_service import FetcherService
from src.services.prisma_service import PrismaService
from src.services.search_service import SearchService
from src.services.streaming_search_service import StreamingSearchService


@dataclass
class FakeSearchSession:
    """In-memory session model used for service tests."""

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
    """Minimal in-memory repository for service-level tests."""

    def __init__(self) -> None:
        self.sessions: dict[str, FakeSearchSession] = {}

    async def create_session(self, request: SearchRequest) -> FakeSearchSession:
        session = FakeSearchSession(
            id=uuid4(),
            query=request.query,
            query_type=request.query_type.value,
            search_mode=request.search_mode.value,
            sources=[source.value for source in request.sources or []],
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


class RecordingFetcherService:
    """Fetcher mock that records call metadata."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def fetch_all_sources(
        self,
        query: str,
        query_type: QueryType,
        search_mode: SearchMode,
        sources: list[SourceType],
        pico=None,
        max_results: int = 100,
    ) -> tuple[list[RawRecord], dict[SourceType, int], list[SourceType]]:
        self.calls.append(
            {
                "query": query,
                "query_type": query_type,
                "search_mode": search_mode,
                "sources": sources,
                "pico": pico,
                "max_results": max_results,
            }
        )
        raw_records = [
            RawRecord(
                source_id=f"{source.value}-1",
                source=source,
                title=f"{source.value} study",
                authors=["Author One"],
                abstract="Long enough abstract text for test behavior checks.",
                oa_status=OAStatus.UNKNOWN,
            )
            for source in sources
        ]
        counts = {source: 1 for source in sources}
        return raw_records, counts, []


class PassthroughDedupService:
    """Simple dedup mock returning one unified record per raw record."""

    def deduplicate(self, records: list[RawRecord]) -> list[UnifiedRecord]:
        return [
            UnifiedRecord(
                id=f"rec-{index}",
                title=record.title,
                authors=record.authors,
                source=record.source,
                sources_found_in=[record.source],
                abstract=record.abstract,
                oa_status=record.oa_status,
            )
            for index, record in enumerate(records)
        ]


class SlowEnrichmentService:
    """Enrichment mock slow enough to keep background task pending."""

    async def enrich_batch(self, records: list[UnifiedRecord]) -> list[EnrichmentResponse]:
        await asyncio.sleep(0.1)
        return [
            EnrichmentResponse(
                id=record.id,
                tldr="summary",
                citation_count=5,
                oa_status=OAStatus.OPEN,
                pdf_url="https://example.org/pdf",
            )
            for record in records
        ]


class SlowOAService:
    """OA mock slow enough to keep background task pending."""

    async def resolve_batch(self, records: list[UnifiedRecord]) -> dict[str, tuple[OAStatus, str | None]]:
        await asyncio.sleep(0.1)
        return {record.id: (OAStatus.OPEN, "https://example.org/pdf") for record in records}


def _build_search_service(
    fetcher: RecordingFetcherService,
    enrichment_service,
    oa_service,
) -> SearchService:
    return SearchService(
        fetcher=fetcher,  # type: ignore[arg-type]
        dedup=PassthroughDedupService(),  # type: ignore[arg-type]
        prisma=PrismaService(),
        search_repo=InMemorySearchRepository(),  # type: ignore[arg-type]
        redis_client=AsyncMock(),
        enrichment_service=enrichment_service,  # type: ignore[arg-type]
        oa_service=oa_service,  # type: ignore[arg-type]
    )


class DummyFetcher:
    """Streaming service only needs the HTTP client holder."""

    def __init__(self) -> None:
        self.client = AsyncMock()


class FakeSourceRepository:
    """Repository mock used by streaming tests."""

    def __init__(self, source: SourceType, max_results_log: list[tuple[SourceType, int]]) -> None:
        self.source = source
        self.max_results_log = max_results_log

    async def search(self, query: str, max_results: int = 100) -> list[RawRecord]:
        self.max_results_log.append((self.source, max_results))
        count = min(2, max_results)
        return [
            RawRecord(
                source_id=f"{self.source.value}-{index}",
                source=self.source,
                title=f"{self.source.value} result {index}",
                authors=["Author One"],
                abstract=f"{query} abstract content",
                oa_status=OAStatus.UNKNOWN,
            )
            for index in range(count)
        ]


class StreamingEnrichmentService:
    """Mock enrichment service for streaming mode tests."""

    async def enrich_batch(self, records: list[UnifiedRecord]) -> list[EnrichmentResponse]:
        return [
            EnrichmentResponse(
                id=record.id,
                tldr=f"TLDR {record.id}",
                citation_count=7,
                oa_status=OAStatus.OPEN,
                pdf_url="https://example.org/pdf",
            )
            for record in records
        ]

    async def enrich_stream(self, records: list[UnifiedRecord]):
        for record in records:
            yield EnrichmentResponse(
                id=record.id,
                tldr=f"TLDR {record.id}",
                citation_count=7,
                oa_status=OAStatus.OPEN,
                pdf_url="https://example.org/pdf",
            )

    async def stream_thinking(self, query: str, records: list[UnifiedRecord], mode: SearchMode):
        if mode is SearchMode.DEEP_THINKING:
            yield "deep analysis chunk"
        elif mode is SearchMode.LIGHT_THINKING:
            yield "quick summary chunk"


class StreamingOAService:
    """Mock OA resolver for streaming mode tests."""

    async def resolve_batch(self, records: list[UnifiedRecord]) -> dict[str, tuple[OAStatus, str | None]]:
        return {record.id: (OAStatus.OPEN, "https://example.org/pdf") for record in records}


@pytest.mark.asyncio
async def test_quick_mode_uses_fast_caps_and_no_enrichment() -> None:
    assert FetcherService._max_results_for_mode(SearchMode.QUICK, 9999) == 50

    fetcher = RecordingFetcherService()
    service = _build_search_service(
        fetcher=fetcher,
        enrichment_service=AsyncMock(),
        oa_service=AsyncMock(),
    )
    request = SearchRequest(query="metformin cardiovascular", search_mode=SearchMode.QUICK)

    await service.execute_search(request)

    assert fetcher.calls
    assert fetcher.calls[0]["search_mode"] is SearchMode.QUICK
    assert len(service._background_tasks) == 0


@pytest.mark.asyncio
async def test_deep_research_mode_keeps_full_requested_depth_without_auto_enrichment() -> None:
    assert FetcherService._max_results_for_mode(SearchMode.DEEP_RESEARCH, 4000) == 4000

    fetcher = RecordingFetcherService()
    service = _build_search_service(
        fetcher=fetcher,
        enrichment_service=AsyncMock(),
        oa_service=AsyncMock(),
    )
    request = SearchRequest(
        query="metformin cardiovascular outcomes",
        search_mode=SearchMode.DEEP_RESEARCH,
        max_results=4000,
    )

    await service.execute_search(request)

    assert fetcher.calls
    assert fetcher.calls[0]["max_results"] == 4000
    assert len(service._background_tasks) == 0


@pytest.mark.asyncio
async def test_deep_analyze_mode_creates_background_enrichment_task() -> None:
    fetcher = RecordingFetcherService()
    service = _build_search_service(
        fetcher=fetcher,
        enrichment_service=SlowEnrichmentService(),
        oa_service=SlowOAService(),
    )
    request = SearchRequest(query="heart failure therapy", search_mode=SearchMode.DEEP_ANALYZE)

    await service.execute_search(request)

    assert service._background_tasks
    await asyncio.gather(*service._background_tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_deep_thinking_stream_emits_enrichment_and_thinking_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services import streaming_search_service as streaming_module

    max_results_log: list[tuple[SourceType, int]] = []

    async def fake_translate_for_all_sources(*, query, query_type, pico=None, sources=None):
        selected_sources = sources or list(SourceType)
        return {source: f"{query} ({source.value})" for source in selected_sources}

    def fake_get_repository(source: SourceType, client) -> FakeSourceRepository:
        _ = client
        return FakeSourceRepository(source=source, max_results_log=max_results_log)

    monkeypatch.setattr(streaming_module, "translate_for_all_sources", fake_translate_for_all_sources)
    monkeypatch.setattr(streaming_module, "get_repository", fake_get_repository)

    service = StreamingSearchService(
        fetcher=DummyFetcher(),  # type: ignore[arg-type]
        dedup=PassthroughDedupService(),  # type: ignore[arg-type]
        prisma=PrismaService(),
        search_repo=InMemorySearchRepository(),  # type: ignore[arg-type]
        redis_client=AsyncMock(),
        enrichment_service=StreamingEnrichmentService(),  # type: ignore[arg-type]
        oa_service=StreamingOAService(),  # type: ignore[arg-type]
    )
    request = SearchRequest(
        query="diabetes pharmacotherapy outcomes in adults",
        search_mode=SearchMode.DEEP_THINKING,
        max_results=120,
    )

    events = [event async for event in service.execute_search_stream(request)]
    event_types = [event.event for event in events]

    assert "record_enriched" in event_types
    assert "thinking" in event_types
    assert "search_completed" in event_types
    assert max_results_log
    assert all(value == 120 for _, value in max_results_log)


@pytest.mark.asyncio
async def test_light_thinking_stream_emits_quick_summary_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services import streaming_search_service as streaming_module

    max_results_log: list[tuple[SourceType, int]] = []

    async def fake_translate_for_all_sources(*, query, query_type, pico=None, sources=None):
        selected_sources = sources or list(SourceType)
        return {source: query for source in selected_sources}

    def fake_get_repository(source: SourceType, client) -> FakeSourceRepository:
        _ = client
        return FakeSourceRepository(source=source, max_results_log=max_results_log)

    monkeypatch.setattr(streaming_module, "translate_for_all_sources", fake_translate_for_all_sources)
    monkeypatch.setattr(streaming_module, "get_repository", fake_get_repository)

    service = StreamingSearchService(
        fetcher=DummyFetcher(),  # type: ignore[arg-type]
        dedup=PassthroughDedupService(),  # type: ignore[arg-type]
        prisma=PrismaService(),
        search_repo=InMemorySearchRepository(),  # type: ignore[arg-type]
        redis_client=AsyncMock(),
        enrichment_service=StreamingEnrichmentService(),  # type: ignore[arg-type]
        oa_service=StreamingOAService(),  # type: ignore[arg-type]
    )
    request = SearchRequest(
        query="long form literature request for light thinking mode checks",
        search_mode=SearchMode.LIGHT_THINKING,
        max_results=500,
    )

    events = [event async for event in service.execute_search_stream(request)]
    event_types = [event.event for event in events]

    assert "thinking" in event_types
    assert "record_enriched" not in event_types
    assert max_results_log
    assert all(value == 30 for _, value in max_results_log)
