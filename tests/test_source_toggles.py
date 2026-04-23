"""Tests for source selection toggles in search requests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from src.core import deps
from src.schemas.enums import OAStatus, QueryType, SearchMode, SourceType
from src.schemas.records import RawRecord, UnifiedRecord
from src.schemas.search import SearchRequest
from src.services.prisma_service import PrismaService
from src.services.search_service import SearchService


@dataclass
class FakeSearchSession:
    """In-memory session model used by source-toggle tests."""

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
    """Minimal in-memory repository for API tests."""

    def __init__(self) -> None:
        self.sessions: dict[str, FakeSearchSession] = {}

    async def create_session(self, request: SearchRequest, *, user_id: object = None) -> FakeSearchSession:
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
        return self.sessions.get(search_id)

    async def store_results(self, search_id: str, records: list[UnifiedRecord]) -> None:
        session = await self.get_session(search_id)
        if session is None:
            return
        session.results = [record.model_dump(mode="json") for record in records]
        session.total_after_dedup = len(records)


class RecordingFetcher:
    """Fetcher mock that records requested sources."""

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
                title=f"{source.value} result",
                authors=["Author"],
                oa_status=OAStatus.UNKNOWN,
            )
            for source in sources
        ]
        source_counts = {source: 1 for source in sources}
        return raw_records, source_counts, []


class PassthroughDedup:
    """Simple dedup mock returning one record per source."""

    def deduplicate(
        self,
        records: list[RawRecord],
        query: str | None = None,  # noqa: ARG002
        query_type: object = None,  # noqa: ARG002
    ) -> list[UnifiedRecord]:
        return [
            UnifiedRecord(
                id=f"rec-{idx}",
                title=record.title,
                authors=record.authors,
                source=record.source,
                sources_found_in=[record.source],
                oa_status=record.oa_status,
            )
            for idx, record in enumerate(records)
        ]


@pytest.fixture
def source_toggle_context(async_client: AsyncClient):
    """Override search dependency with deterministic mocked internals."""
    from src.main import app

    fetcher = RecordingFetcher()
    service = SearchService(
        fetcher=fetcher,  # type: ignore[arg-type]
        dedup=PassthroughDedup(),  # type: ignore[arg-type]
        prisma=PrismaService(),
        search_repo=InMemorySearchRepository(),  # type: ignore[arg-type]
        redis_client=AsyncMock(),
        enrichment_service=AsyncMock(),
        oa_service=AsyncMock(),
    )

    app.dependency_overrides[deps.get_search_service] = lambda: service
    yield async_client, fetcher
    app.dependency_overrides.pop(deps.get_search_service, None)


@pytest.mark.asyncio
async def test_sources_single_pubmed_queries_only_pubmed(source_toggle_context) -> None:
    client, fetcher = source_toggle_context
    response = await client.post(
        "/api/v1/search",
        json={"query": "metformin cardiovascular", "sources": ["pubmed"]},
    )

    assert response.status_code == 200
    assert fetcher.calls
    assert fetcher.calls[-1]["sources"] == [SourceType.PUBMED]


@pytest.mark.asyncio
async def test_sources_pubmed_and_openalex_queries_both(source_toggle_context) -> None:
    client, fetcher = source_toggle_context
    response = await client.post(
        "/api/v1/search",
        json={"query": "metformin cardiovascular", "sources": ["pubmed", "openalex"]},
    )

    assert response.status_code == 200
    assert fetcher.calls
    assert fetcher.calls[-1]["sources"] == [SourceType.PUBMED, SourceType.OPENALEX]


@pytest.mark.asyncio
async def test_sources_none_defaults_to_all_sources(source_toggle_context) -> None:
    client, fetcher = source_toggle_context
    response = await client.post(
        "/api/v1/search",
        json={"query": "metformin cardiovascular", "sources": None},
    )

    assert response.status_code == 200
    assert fetcher.calls
    assert fetcher.calls[-1]["sources"] == list(SourceType)


@pytest.mark.asyncio
async def test_sources_empty_list_returns_422(source_toggle_context) -> None:
    client, _fetcher = source_toggle_context
    response = await client.post(
        "/api/v1/search",
        json={"query": "metformin cardiovascular", "sources": []},
    )

    assert response.status_code == 422
