"""Integration tests for enrichment endpoint behavior."""

from __future__ import annotations

import time

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_enrichment_returns_tldr_and_citations(
    integration_client: AsyncClient,
    run_search,
) -> None:
    search_id, response = await run_search(query="metformin cardiovascular", sources=["pubmed"])
    assert response.status_code == 200

    results_response = await integration_client.get(f"/api/v1/results/{search_id}")
    results_body = results_response.json()
    assert results_response.status_code == 200
    record_id = results_body["records"][0]["id"]

    enrichment_response = await integration_client.get(f"/api/v1/enrichment/{search_id}/{record_id}")
    enrichment_body = enrichment_response.json()
    assert enrichment_response.status_code == 200
    assert enrichment_body["id"] == record_id
    assert enrichment_body["tldr"]
    assert isinstance(enrichment_body["citation_count"], int)


@pytest.mark.asyncio
async def test_oa_resolution_via_enrichment(
    integration_client: AsyncClient,
    run_search,
) -> None:
    search_id, response = await run_search(query="metformin cardiovascular", sources=["pubmed"])
    assert response.status_code == 200

    results_response = await integration_client.get(f"/api/v1/results/{search_id}")
    results_body = results_response.json()
    record_id = results_body["records"][0]["id"]

    enrichment_response = await integration_client.get(f"/api/v1/enrichment/{search_id}/{record_id}")
    enrichment_body = enrichment_response.json()
    assert enrichment_response.status_code == 200
    assert enrichment_body["oa_status"] == "open"
    assert enrichment_body["pdf_url"] is not None
    assert enrichment_body["pdf_url"].startswith("https://")


@pytest.mark.asyncio
async def test_enrichment_cache_hit_returns_same_data(
    integration_client: AsyncClient,
    run_search,
    mock_external_apis: dict,
) -> None:
    search_id, response = await run_search(query="metformin cardiovascular", sources=["pubmed"])
    assert response.status_code == 200
    results_response = await integration_client.get(f"/api/v1/results/{search_id}")
    record_id = results_response.json()["records"][0]["id"]

    s2_calls_before = len(mock_external_apis["semantic_scholar"].calls)
    first_started = time.perf_counter()
    first_response = await integration_client.get(f"/api/v1/enrichment/{search_id}/{record_id}")
    first_elapsed = time.perf_counter() - first_started
    first_body = first_response.json()

    s2_calls_after_first = len(mock_external_apis["semantic_scholar"].calls)
    second_started = time.perf_counter()
    second_response = await integration_client.get(f"/api/v1/enrichment/{search_id}/{record_id}")
    second_elapsed = time.perf_counter() - second_started
    second_body = second_response.json()
    s2_calls_after_second = len(mock_external_apis["semantic_scholar"].calls)

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_body == second_body
    assert second_elapsed <= first_elapsed
    assert s2_calls_after_first == s2_calls_before + 1
    assert s2_calls_after_second == s2_calls_after_first

