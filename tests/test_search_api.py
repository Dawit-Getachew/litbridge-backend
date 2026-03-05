"""API tests for search execution, status, and cursor-paginated results."""

from __future__ import annotations

import base64
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
    """In-memory session model used by API tests."""

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
    """Minimal in-memory search repository for endpoint tests."""

    def __init__(self) -> None:
        self.sessions: dict[str, FakeSearchSession] = {}

    async def create_session(self, request: SearchRequest) -> FakeSearchSession:
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

    async def get_results_page(
        self,
        search_id: str,
        cursor: str | None,
        page_size: int = 20,
    ) -> tuple[list[UnifiedRecord], str | None]:
        session = await self.get_session(search_id)
        if session is None:
            return [], None

        offset = self._decode_cursor(cursor)
        page_data = session.results[offset : offset + page_size]
        records = [UnifiedRecord.model_validate(item) for item in page_data]
        next_offset = offset + len(records)
        next_cursor = self._encode_cursor(next_offset) if next_offset < len(session.results) else None
        return records, next_cursor

    @staticmethod
    def _encode_cursor(offset: int) -> str:
        return base64.urlsafe_b64encode(str(offset).encode("utf-8")).decode("utf-8")

    @staticmethod
    def _decode_cursor(cursor: str | None) -> int:
        if cursor is None:
            return 0
        try:
            return int(base64.urlsafe_b64decode(cursor.encode("utf-8")).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return 0


class MockFetcherService:
    """Fetcher mock that tracks requested source sets."""

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
                title=f"{source.value} trial",
                authors=["Author One"],
                oa_status=OAStatus.UNKNOWN,
            )
            for source in sources
        ]
        source_counts = {source: 1 for source in sources}
        return raw_records, source_counts, []


class MockDedupService:
    """Dedup mock returning deterministic unified records for pagination."""

    def __init__(self, record_count: int = 25) -> None:
        self.record_count = record_count

    def deduplicate(self, _records: list[RawRecord]) -> list[UnifiedRecord]:
        sources = list(SourceType)
        return [
            UnifiedRecord(
                id=f"rec-{index}",
                title=f"Unified Study {index}",
                authors=["Author One"],
                source=sources[index % len(sources)],
                sources_found_in=[sources[index % len(sources)]],
            )
            for index in range(self.record_count)
        ]


@pytest.fixture
def search_api_context(async_client: AsyncClient):
    """Override search dependency with real service + mocked internals."""
    from src.main import app

    repository = InMemorySearchRepository()
    fetcher = MockFetcherService()
    dedup = MockDedupService(record_count=25)
    service = SearchService(
        fetcher=fetcher,  # type: ignore[arg-type]
        dedup=dedup,  # type: ignore[arg-type]
        prisma=PrismaService(),
        search_repo=repository,  # type: ignore[arg-type]
        redis_client=AsyncMock(),
        enrichment_service=AsyncMock(),
        oa_service=AsyncMock(),
    )

    app.dependency_overrides[deps.get_search_service] = lambda: service
    yield async_client, fetcher
    app.dependency_overrides.pop(deps.get_search_service, None)


@pytest.mark.asyncio
async def test_post_search_with_free_text_query_returns_search_id(search_api_context) -> None:
    client, _fetcher = search_api_context
    response = await client.post(
        "/api/v1/search",
        json={"query": "metformin cardiovascular", "search_mode": "quick"},
    )
    body = response.json()

    assert response.status_code == 200
    assert "search_id" in body
    assert isinstance(body["search_id"], str)


@pytest.mark.asyncio
async def test_post_search_with_pico_query_returns_search_id(search_api_context) -> None:
    client, _fetcher = search_api_context
    response = await client.post(
        "/api/v1/search",
        json={
            "query": "heart failure adults",
            "query_type": "structured",
            "pico": {
                "population": "adults with heart failure",
                "intervention": "metformin",
                "comparison": "placebo",
                "outcome": "cardiovascular mortality",
            },
        },
    )
    body = response.json()

    assert response.status_code == 200
    assert "search_id" in body


@pytest.mark.asyncio
async def test_post_search_with_boolean_query_returns_search_id(search_api_context) -> None:
    client, _fetcher = search_api_context
    response = await client.post(
        "/api/v1/search",
        json={
            "query": "metformin AND cardiovascular NOT pediatric",
            "query_type": "boolean",
        },
    )
    body = response.json()

    assert response.status_code == 200
    assert "search_id" in body


@pytest.mark.asyncio
async def test_post_search_with_selected_sources_only_queries_those_sources(search_api_context) -> None:
    client, fetcher = search_api_context
    response = await client.post(
        "/api/v1/search",
        json={
            "query": "metformin cardiovascular",
            "sources": ["pubmed", "openalex"],
        },
    )

    assert response.status_code == 200
    assert fetcher.calls
    called_sources = fetcher.calls[-1]["sources"]
    assert called_sources == [SourceType.PUBMED, SourceType.OPENALEX]


@pytest.mark.asyncio
async def test_post_search_with_invalid_body_returns_422(search_api_context) -> None:
    client, _fetcher = search_api_context
    response = await client.post("/api/v1/search", json={})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_results_returns_first_page_with_records(search_api_context) -> None:
    client, _fetcher = search_api_context
    search_response = await client.post("/api/v1/search", json={"query": "metformin cardiovascular"})
    search_id = search_response.json()["search_id"]

    response = await client.get(f"/api/v1/results/{search_id}")
    body = response.json()

    assert response.status_code == 200
    assert body["search_id"] == search_id
    assert len(body["records"]) == 20
    assert body["next_cursor"] is not None


@pytest.mark.asyncio
async def test_get_results_with_cursor_returns_next_page(search_api_context) -> None:
    client, _fetcher = search_api_context
    search_response = await client.post("/api/v1/search", json={"query": "metformin cardiovascular"})
    search_id = search_response.json()["search_id"]

    first_page = await client.get(f"/api/v1/results/{search_id}")
    next_cursor = first_page.json()["next_cursor"]
    second_page = await client.get(f"/api/v1/results/{search_id}", params={"cursor": next_cursor})
    body = second_page.json()

    assert second_page.status_code == 200
    assert body["search_id"] == search_id
    assert len(body["records"]) == 5
    assert body["next_cursor"] is None


@pytest.mark.asyncio
async def test_get_results_with_bad_search_id_returns_404(search_api_context) -> None:
    client, _fetcher = search_api_context
    response = await client.get("/api/v1/results/not-a-real-id")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_search_status_returns_status_payload(search_api_context) -> None:
    client, _fetcher = search_api_context
    search_response = await client.post("/api/v1/search", json={"query": "metformin cardiovascular"})
    search_id = search_response.json()["search_id"]

    response = await client.get(f"/api/v1/search/{search_id}/status")
    body = response.json()

    assert response.status_code == 200
    assert body["search_id"] == search_id
    assert body["status"] == "completed"
    assert body["total_count"] == 25
    assert body["progress_pct"] == 100
