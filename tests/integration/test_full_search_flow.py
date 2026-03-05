"""Integration tests for full search flow and streaming behavior."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from src.schemas.enums import SourceType
from tests.integration.conftest import parse_sse_events

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_free_text_search_dedup_paginated_results(
    integration_client: AsyncClient,
    run_search,
) -> None:
    search_id, search_response = await run_search(query="metformin cardiovascular")
    assert search_response.status_code == 200
    assert isinstance(search_id, str) and search_id

    first_page_response = await integration_client.get(f"/api/v1/results/{search_id}")
    first_page = first_page_response.json()
    assert first_page_response.status_code == 200
    assert first_page["search_id"] == search_id
    assert first_page["records"]

    dois = [record.get("doi") for record in first_page["records"] if record.get("doi")]
    assert len(dois) == len(set(dois))

    if first_page["next_cursor"] is not None:
        second_page_response = await integration_client.get(
            f"/api/v1/results/{search_id}",
            params={"cursor": first_page["next_cursor"]},
        )
        second_page = second_page_response.json()
        assert second_page_response.status_code == 200
        assert second_page["search_id"] == search_id
        assert second_page["records"]


@pytest.mark.asyncio
async def test_pico_search_adapts_queries_correctly(
    integration_client: AsyncClient,
    mock_external_apis: dict,
) -> None:
    response = await integration_client.post(
        "/api/v1/search",
        json={
            "query": "PICO search",
            "query_type": "structured",
            "pico": {
                "population": "type 2 diabetes",
                "intervention": "metformin",
            },
            "search_mode": "deep_research",
        },
    )
    body = response.json()
    assert response.status_code == 200
    assert isinstance(body.get("search_id"), str)

    pubmed_term = mock_external_apis["pubmed_esearch"].calls[-1].request.url.params["term"].lower()
    openalex_query = mock_external_apis["openalex_works"].calls[-1].request.url.params["search"].lower()
    europe_query = mock_external_apis["europepmc_search"].calls[-1].request.url.params["query"].lower()
    ct_query = mock_external_apis["clinicaltrials_search"].calls[-1].request.url.params["query.term"].lower()

    for translated_query in (pubmed_term, openalex_query, europe_query, ct_query):
        assert "diabetes" in translated_query
        assert "metformin" in translated_query

    results_response = await integration_client.get(f"/api/v1/results/{body['search_id']}")
    results_body = results_response.json()
    assert results_response.status_code == 200
    assert results_body["records"]


@pytest.mark.asyncio
async def test_source_toggle_only_selected_sources(
    integration_client: AsyncClient,
    mock_external_apis: dict,
) -> None:
    response = await integration_client.post(
        "/api/v1/search",
        json={
            "query": "metformin cardiovascular",
            "sources": ["pubmed"],
            "search_mode": "quick",
        },
    )
    search_id = response.json()["search_id"]
    assert response.status_code == 200

    results_response = await integration_client.get(f"/api/v1/results/{search_id}")
    results = results_response.json()["records"]
    assert results_response.status_code == 200
    assert results
    assert all(record["source"] == "pubmed" for record in results)
    assert all(set(record["sources_found_in"]) == {"pubmed"} for record in results)

    assert len(mock_external_apis["pubmed_esearch"].calls) >= 1
    assert len(mock_external_apis["openalex_works"].calls) == 0
    assert len(mock_external_apis["europepmc_search"].calls) == 0
    assert len(mock_external_apis["clinicaltrials_search"].calls) == 0


@pytest.mark.asyncio
async def test_streaming_search_event_sequence(integration_client: AsyncClient) -> None:
    async with integration_client.stream(
        "POST",
        "/api/v1/search/stream",
        json={"query": "metformin cardiovascular", "search_mode": "quick"},
    ) as response:
        payload = (await response.aread()).decode("utf-8")

    assert response.status_code == 200
    events = parse_sse_events(payload)
    event_names = [name for name, _data in events]

    assert event_names[0] == "search_started"
    assert event_names[-1] == "search_completed"
    assert "dedup_completed" in event_names
    assert event_names.count("source_completed") >= 1

    last_source_event_index = max(
        index
        for index, event_name in enumerate(event_names)
        if event_name in {"source_completed", "source_failed"}
    )
    dedup_index = event_names.index("dedup_completed")
    assert dedup_index > last_source_event_index

    started_data = events[0][1]
    completed_data = events[-1][1]
    assert started_data["search_id"] == completed_data["search_id"]


@pytest.mark.asyncio
async def test_deep_thinking_streaming_includes_thinking_events(integration_client: AsyncClient) -> None:
    async with integration_client.stream(
        "POST",
        "/api/v1/search/stream",
        json={
            "query": "metformin cardiovascular outcomes and mortality",
            "search_mode": "deep_thinking",
            "max_results": 5,
        },
    ) as response:
        payload = (await response.aread()).decode("utf-8")

    assert response.status_code == 200
    events = parse_sse_events(payload)
    event_names = [name for name, _data in events]
    assert "dedup_completed" in event_names
    assert "thinking" in event_names

    dedup_index = event_names.index("dedup_completed")
    first_thinking_index = event_names.index("thinking")
    assert first_thinking_index > dedup_index


@pytest.mark.asyncio
async def test_search_preview_returns_translations(integration_client: AsyncClient) -> None:
    response = await integration_client.post(
        "/api/v1/search/preview",
        json={"query": "metformin type 2 diabetes cardiovascular"},
    )
    body = response.json()

    assert response.status_code == 200
    assert "translations" in body
    assert set(body["translations"].keys()) == {source.value for source in SourceType}

