"""API contract stability tests for frontend integration safety."""

from __future__ import annotations

import re

import pytest
from httpx import AsyncClient

from src.schemas.enums import OAStatus, SearchMode, SourceType
from tests.integration.conftest import parse_sse_events

pytestmark = pytest.mark.integration


def _assert_snake_case_keys(payload):
    if isinstance(payload, dict):
        for key, value in payload.items():
            assert re.fullmatch(r"[a-z][a-z0-9_]*", key), f"Found non-snake_case key: {key}"
            _assert_snake_case_keys(value)
    elif isinstance(payload, list):
        for item in payload:
            _assert_snake_case_keys(item)


@pytest.mark.asyncio
async def test_search_response_shape(integration_client: AsyncClient, run_search) -> None:
    search_id, response = await run_search(query="metformin cardiovascular")
    body = response.json()

    assert response.status_code == 200
    assert set(body.keys()) == {"search_id"}
    assert body["search_id"] == search_id
    assert isinstance(body["search_id"], str)


@pytest.mark.asyncio
async def test_results_response_shape(integration_client: AsyncClient, run_search) -> None:
    search_id, _response = await run_search(query="metformin cardiovascular")
    results_response = await integration_client.get(f"/api/v1/results/{search_id}")
    body = results_response.json()

    assert results_response.status_code == 200
    assert set(body.keys()) == {"search_id", "total_count", "records", "next_cursor"}
    assert isinstance(body["search_id"], str)
    assert isinstance(body["total_count"], int)
    assert isinstance(body["records"], list)
    assert isinstance(body["next_cursor"], str) or body["next_cursor"] is None


@pytest.mark.asyncio
async def test_unified_record_has_all_required_fields(integration_client: AsyncClient, run_search) -> None:
    search_id, _response = await run_search(query="metformin cardiovascular")
    results = (await integration_client.get(f"/api/v1/results/{search_id}")).json()["records"]
    assert results

    required_fields = {"id", "title", "authors", "source", "oa_status"}
    for record in results:
        assert required_fields.issubset(record.keys())
        assert isinstance(record["id"], str)
        assert isinstance(record["title"], str)
        assert isinstance(record["authors"], list)
        assert all(isinstance(author, str) for author in record["authors"])
        assert record["source"] in {source.value for source in SourceType}
        assert record["oa_status"] in {status.value for status in OAStatus}


@pytest.mark.asyncio
async def test_optional_fields_correct_types_or_absent(integration_client: AsyncClient, run_search) -> None:
    search_id, _response = await run_search(query="metformin cardiovascular")
    results = (await integration_client.get(f"/api/v1/results/{search_id}")).json()["records"]
    assert results
    record = results[0]

    expected_optional_types = {
        "journal": str,
        "year": int,
        "doi": str,
        "pmid": str,
        "tldr": str,
        "citation_count": int,
        "pdf_url": str,
        "abstract": str,
        "duplicate_cluster_id": str,
        "prisma_stage": str,
    }

    for field, field_type in expected_optional_types.items():
        assert field in record
        if record[field] is not None:
            assert isinstance(record[field], field_type)


@pytest.mark.asyncio
async def test_enrichment_response_shape(integration_client: AsyncClient, run_search) -> None:
    search_id, _response = await run_search(query="metformin cardiovascular", sources=["pubmed"])
    results = (await integration_client.get(f"/api/v1/results/{search_id}")).json()["records"]
    record_id = results[0]["id"]

    response = await integration_client.get(f"/api/v1/enrichment/{search_id}/{record_id}")
    body = response.json()
    assert response.status_code == 200
    assert set(body.keys()) == {"id", "tldr", "citation_count", "oa_status", "pdf_url"}
    assert isinstance(body["id"], str)
    assert body["oa_status"] in {status.value for status in OAStatus}
    assert body["tldr"] is None or isinstance(body["tldr"], str)
    assert body["citation_count"] is None or isinstance(body["citation_count"], int)
    assert body["pdf_url"] is None or isinstance(body["pdf_url"], str)


@pytest.mark.asyncio
async def test_prisma_response_shape(integration_client: AsyncClient, run_search) -> None:
    search_id, _response = await run_search(query="metformin cardiovascular")
    response = await integration_client.get(f"/api/v1/prisma/{search_id}")
    body = response.json()

    assert response.status_code == 200
    assert set(body.keys()) == {
        "identified",
        "after_deduplication",
        "screened",
        "excluded",
        "oa_retrieved",
    }
    assert all(isinstance(value, int) for value in body.values())


@pytest.mark.asyncio
async def test_sse_stream_events_match_stream_event_format(integration_client: AsyncClient) -> None:
    async with integration_client.stream(
        "POST",
        "/api/v1/search/stream",
        json={"query": "metformin cardiovascular", "search_mode": "quick"},
    ) as response:
        raw_stream = (await response.aread()).decode("utf-8")

    assert response.status_code == 200
    for line in raw_stream.splitlines():
        if not line:
            continue
        assert line.startswith("event: ") or line.startswith("data: ")

    events = parse_sse_events(raw_stream)
    assert events
    assert all(isinstance(event_name, str) for event_name, _ in events)
    assert all(isinstance(data, dict) for _, data in events)


@pytest.mark.asyncio
async def test_all_enum_values_are_lowercase_strings(integration_client: AsyncClient, run_search) -> None:
    search_id, _response = await run_search(query="metformin cardiovascular")
    records = (await integration_client.get(f"/api/v1/results/{search_id}")).json()["records"]
    assert records

    for record in records:
        assert record["source"] == record["source"].lower()
        assert record["oa_status"] == record["oa_status"].lower()

    async with integration_client.stream(
        "POST",
        "/api/v1/search/stream",
        json={"query": "metformin cardiovascular", "search_mode": SearchMode.DEEP_THINKING.value, "max_results": 5},
    ) as stream_response:
        stream_body = (await stream_response.aread()).decode("utf-8")
    events = parse_sse_events(stream_body)
    started_event = next(data for event_name, data in events if event_name == "search_started")
    assert started_event["search_mode"] == started_event["search_mode"].lower()


@pytest.mark.asyncio
async def test_snake_case_field_names_throughout(integration_client: AsyncClient, run_search) -> None:
    search_id, search_response = await run_search(query="metformin cardiovascular")
    results_response = await integration_client.get(f"/api/v1/results/{search_id}")
    prisma_response = await integration_client.get(f"/api/v1/prisma/{search_id}")
    enrichment_record_id = results_response.json()["records"][0]["id"]
    enrichment_response = await integration_client.get(f"/api/v1/enrichment/{search_id}/{enrichment_record_id}")

    _assert_snake_case_keys(search_response.json())
    _assert_snake_case_keys(results_response.json())
    _assert_snake_case_keys(prisma_response.json())
    _assert_snake_case_keys(enrichment_response.json())


@pytest.mark.asyncio
async def test_next_cursor_is_null_on_last_page(integration_client: AsyncClient, run_search) -> None:
    search_id, _response = await run_search(query="metformin cardiovascular")

    cursor = None
    last_page = None
    for _ in range(10):
        response = await integration_client.get(f"/api/v1/results/{search_id}", params={"cursor": cursor})
        assert response.status_code == 200
        page = response.json()
        last_page = page
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert last_page is not None
    assert "next_cursor" in last_page
    assert last_page["next_cursor"] is None


@pytest.mark.asyncio
async def test_empty_search_returns_zero_total_and_empty_records(
    integration_client: AsyncClient,
    run_search,
) -> None:
    search_id, search_response = await run_search(query="   ", search_mode="quick")
    assert search_response.status_code == 200

    results_response = await integration_client.get(f"/api/v1/results/{search_id}")
    body = results_response.json()
    assert results_response.status_code == 200
    assert body["total_count"] == 0
    assert body["records"] == []

