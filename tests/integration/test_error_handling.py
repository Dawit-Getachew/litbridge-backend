"""Integration tests for API error handling behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from src.core import deps
from src.core.exceptions import RateLimitError, SourceFetchError
from src.schemas.enums import OAStatus, SourceType
from src.schemas.records import RawRecord
from src.services.search_service import SearchService

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_all_sources_fail_returns_502(integration_context) -> None:
    from src.main import app

    class AlwaysFailSearchService:
        async def execute_search(self, _request):
            raise SourceFetchError(source="all", status_code=502, message="All external sources failed")

    app.dependency_overrides[deps.get_search_service] = lambda: AlwaysFailSearchService()
    try:
        response = await integration_context.client.post(
            "/api/v1/search",
            json={"query": "metformin cardiovascular"},
        )
    finally:
        app.dependency_overrides[deps.get_search_service] = lambda: integration_context.search_service

    assert response.status_code == 502
    assert "failed" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_one_source_fails_others_succeed_returns_partial(integration_context) -> None:
    from src.main import app

    class PartialFailureFetcher:
        async def fetch_all_sources(
            self,
            query,
            query_type,
            search_mode,
            sources,
            pico=None,
            max_results=100,
        ):
            _ = (query, query_type, search_mode, pico, max_results)
            records = [
                RawRecord(
                    source_id="pmid-1",
                    source=SourceType.PUBMED,
                    title="Metformin outcomes in adults",
                    authors=["Doe J"],
                    doi="10.5555/partial-1",
                    oa_status=OAStatus.UNKNOWN,
                )
            ]
            counts = {source: 0 for source in sources}
            counts[SourceType.PUBMED] = 1
            return records, counts, [SourceType.OPENALEX]

    service = SearchService(
        fetcher=PartialFailureFetcher(),  # type: ignore[arg-type]
        dedup=integration_context.search_service.dedup,
        prisma=integration_context.search_service.prisma,
        search_repo=integration_context.search_repo,  # type: ignore[arg-type]
        redis_client=integration_context.redis_client,  # type: ignore[arg-type]
        enrichment_service=integration_context.search_service.enrichment_service,
        oa_service=integration_context.search_service.oa_service,
    )

    app.dependency_overrides[deps.get_search_service] = lambda: service
    try:
        search_response = await integration_context.client.post(
            "/api/v1/search",
            json={"query": "metformin cardiovascular", "sources": ["pubmed", "openalex"]},
        )
    finally:
        app.dependency_overrides[deps.get_search_service] = lambda: integration_context.search_service

    assert search_response.status_code == 200
    search_id = search_response.json()["search_id"]

    status_response = await integration_context.client.get(f"/api/v1/search/{search_id}/status")
    status_body = status_response.json()
    assert status_response.status_code == 200
    assert "openalex" in status_body["sources_failed"]

    results_response = await integration_context.client.get(f"/api/v1/results/{search_id}")
    assert results_response.status_code == 200
    assert results_response.json()["records"]


@pytest.mark.asyncio
async def test_invalid_search_id_returns_404(integration_client: AsyncClient) -> None:
    results_response = await integration_client.get("/api/v1/results/not-a-real-id")
    enrichment_response = await integration_client.get("/api/v1/enrichment/not-a-real-id/rec-1")
    prisma_response = await integration_client.get("/api/v1/prisma/not-a-real-id")

    assert results_response.status_code == 404
    assert enrichment_response.status_code == 404
    assert prisma_response.status_code == 404


@pytest.mark.asyncio
async def test_malformed_request_body_returns_422(integration_client: AsyncClient) -> None:
    empty_body_response = await integration_client.post("/api/v1/search", json={})
    empty_sources_response = await integration_client.post(
        "/api/v1/search",
        json={"query": "metformin", "sources": []},
    )
    bad_structured_response = await integration_client.post(
        "/api/v1/search",
        json={"query": "metformin", "query_type": "structured"},
    )

    assert empty_body_response.status_code == 422
    assert empty_sources_response.status_code == 422
    assert bad_structured_response.status_code == 422
    assert "detail" in empty_body_response.json()


@pytest.mark.asyncio
async def test_rate_limit_simulation_returns_429_with_retry_after(integration_context) -> None:
    from src.main import app

    class RateLimitedSearchService:
        async def execute_search(self, _request):
            raise RateLimitError(source="pubmed", retry_after=2.0, message="Rate limited by pubmed")

    app.dependency_overrides[deps.get_search_service] = lambda: RateLimitedSearchService()
    try:
        response = await integration_context.client.post(
            "/api/v1/search",
            json={"query": "metformin cardiovascular"},
        )
    finally:
        app.dependency_overrides[deps.get_search_service] = lambda: integration_context.search_service

    assert response.status_code == 429
    assert "retry-after" in {key.lower() for key in response.headers.keys()}

