"""API tests for per-record enrichment endpoint."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

from src.core import deps
from src.core.redis import build_cache_key
from src.schemas.enrichment import EnrichmentResponse
from src.schemas.enums import OAStatus, SourceType
from src.schemas.records import UnifiedRecord


@dataclass
class FakeSearchSession:
    """In-memory session model used by enrichment endpoint tests."""

    id: UUID
    results: list[dict]


class InMemorySearchRepository:
    """Minimal repository supporting session lookup by search id."""

    def __init__(self, session: FakeSearchSession) -> None:
        self.session = session

    async def get_session(self, search_id: str) -> FakeSearchSession | None:
        if search_id == str(self.session.id):
            return self.session
        return None


class InMemoryRedis:
    """Very small async Redis-like store for endpoint tests."""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    async def get(self, key: str) -> bytes | None:
        return self._store.get(key)

    async def set(self, key: str, value: bytes, ex: int | None = None) -> bool:  # noqa: ARG002
        self._store[key] = value
        return True

    async def ping(self) -> bool:
        return True


class StubEnrichmentService:
    """Enrichment mock that writes response payloads into Redis cache."""

    def __init__(self, redis_client: InMemoryRedis) -> None:
        self.redis_client = redis_client
        self.calls = 0

    async def enrich_record(self, record: UnifiedRecord) -> EnrichmentResponse:
        self.calls += 1
        await asyncio.sleep(0.02)
        payload = EnrichmentResponse(
            id=record.id,
            tldr=f"TLDR for {record.id}",
            citation_count=42,
            oa_status=OAStatus.UNKNOWN,
            pdf_url=None,
        )
        cache_key = build_cache_key("enrichment", record.id)
        await self.redis_client.set(
            cache_key,
            json.dumps(payload.model_dump(mode="json")).encode("utf-8"),
            ex=3600,
        )
        return payload


class StubOAService:
    """OA mock returning deterministic open-access resolution."""

    async def resolve_oa(self, _record: UnifiedRecord) -> tuple[OAStatus, str | None]:
        return OAStatus.OPEN, "https://example.org/fulltext.pdf"


@pytest.fixture
def enrichment_api_context(async_client: AsyncClient):
    """Override dependencies with in-memory repository/services."""
    from src.main import app

    record = UnifiedRecord(
        id="rec-1",
        title="Metformin cardiovascular outcomes",
        authors=["Author One"],
        source=SourceType.PUBMED,
        sources_found_in=[SourceType.PUBMED],
    )
    session = FakeSearchSession(id=uuid4(), results=[record.model_dump(mode="json")])
    search_repo = InMemorySearchRepository(session)
    redis_client = InMemoryRedis()
    enrichment_service = StubEnrichmentService(redis_client)
    oa_service = StubOAService()

    app.dependency_overrides[deps.get_search_repo] = lambda: search_repo
    app.dependency_overrides[deps.get_redis] = lambda: redis_client
    app.dependency_overrides[deps.get_enrichment_service] = lambda: enrichment_service
    app.dependency_overrides[deps.get_oa_service] = lambda: oa_service

    yield async_client, str(session.id), enrichment_service
    app.dependency_overrides.pop(deps.get_search_repo, None)
    app.dependency_overrides.pop(deps.get_redis, None)
    app.dependency_overrides.pop(deps.get_enrichment_service, None)
    app.dependency_overrides.pop(deps.get_oa_service, None)


@pytest.mark.asyncio
async def test_get_enrichment_returns_enrichment_payload(enrichment_api_context) -> None:
    client, search_id, _service = enrichment_api_context
    response = await client.get(f"/api/v1/enrichment/{search_id}/rec-1")
    body = response.json()

    assert response.status_code == 200
    assert body["id"] == "rec-1"
    assert body["tldr"] == "TLDR for rec-1"
    assert body["citation_count"] == 42
    assert body["oa_status"] == "open"
    assert body["pdf_url"] == "https://example.org/fulltext.pdf"


@pytest.mark.asyncio
async def test_get_enrichment_with_unknown_search_id_returns_404(enrichment_api_context) -> None:
    client, _search_id, _service = enrichment_api_context
    response = await client.get(f"/api/v1/enrichment/{uuid4()}/rec-1")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_enrichment_with_unknown_record_id_returns_404(enrichment_api_context) -> None:
    client, search_id, _service = enrichment_api_context
    response = await client.get(f"/api/v1/enrichment/{search_id}/missing-record")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_enrichment_second_call_is_faster_due_to_cache(enrichment_api_context) -> None:
    client, search_id, enrichment_service = enrichment_api_context

    start_one = time.perf_counter()
    first_response = await client.get(f"/api/v1/enrichment/{search_id}/rec-1")
    first_elapsed = time.perf_counter() - start_one

    start_two = time.perf_counter()
    second_response = await client.get(f"/api/v1/enrichment/{search_id}/rec-1")
    second_elapsed = time.perf_counter() - start_two

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert second_elapsed < first_elapsed
    assert enrichment_service.calls == 1
