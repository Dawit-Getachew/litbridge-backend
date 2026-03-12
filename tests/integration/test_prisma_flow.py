"""Integration tests for PRISMA counts endpoint."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


def _assert_counts_shape(payload: dict) -> None:
    fields = {
        "identified",
        "after_deduplication",
        "screened",
        "excluded",
        "oa_retrieved",
    }
    assert set(payload.keys()) == fields
    for field in fields:
        assert isinstance(payload[field], int)
        assert payload[field] >= 0


@pytest.mark.asyncio
async def test_prisma_counts_without_filters(integration_client: AsyncClient, run_search) -> None:
    search_id, response = await run_search(query="metformin cardiovascular")
    assert response.status_code == 200

    prisma_response = await integration_client.get(f"/api/v1/prisma/{search_id}")
    prisma_body = prisma_response.json()
    assert prisma_response.status_code == 200
    _assert_counts_shape(prisma_body)
    assert prisma_body["identified"] >= prisma_body["after_deduplication"]


@pytest.mark.asyncio
async def test_prisma_counts_with_year_filter(integration_client: AsyncClient, run_search) -> None:
    search_id, response = await run_search(query="metformin cardiovascular")
    assert response.status_code == 200

    unfiltered_response = await integration_client.get(f"/api/v1/prisma/{search_id}")
    filtered_response = await integration_client.get(
        f"/api/v1/prisma/{search_id}",
        params={"year_from": 2020, "year_to": 2024},
    )
    unfiltered = unfiltered_response.json()
    filtered = filtered_response.json()

    assert filtered_response.status_code == 200
    _assert_counts_shape(filtered)
    assert filtered["after_deduplication"] <= unfiltered["after_deduplication"]


@pytest.mark.asyncio
async def test_prisma_counts_with_source_filter(integration_client: AsyncClient, run_search) -> None:
    search_id, response = await run_search(query="metformin cardiovascular")
    assert response.status_code == 200

    unfiltered_response = await integration_client.get(f"/api/v1/prisma/{search_id}")
    filtered_response = await integration_client.get(
        f"/api/v1/prisma/{search_id}",
        params={"sources": "pubmed"},
    )
    unfiltered = unfiltered_response.json()
    filtered = filtered_response.json()

    assert filtered_response.status_code == 200
    _assert_counts_shape(filtered)
    assert filtered["excluded"] >= unfiltered["excluded"]
    assert filtered["oa_retrieved"] <= unfiltered["oa_retrieved"]


@pytest.mark.asyncio
async def test_prisma_counts_with_oa_filter(integration_client: AsyncClient, run_search) -> None:
    search_id, response = await run_search(query="open access metformin", sources=["openalex"])
    assert response.status_code == 200

    filtered_response = await integration_client.get(
        f"/api/v1/prisma/{search_id}",
        params={"open_access_only": "true"},
    )
    filtered = filtered_response.json()

    assert filtered_response.status_code == 200
    _assert_counts_shape(filtered)
    assert filtered["oa_retrieved"] == filtered["after_deduplication"]


@pytest.mark.asyncio
async def test_prisma_counts_with_study_type_filter(integration_client: AsyncClient, run_search) -> None:
    search_id, response = await run_search(query="metformin cardiovascular")
    assert response.status_code == 200

    unfiltered_response = await integration_client.get(f"/api/v1/prisma/{search_id}")
    filtered_response = await integration_client.get(
        f"/api/v1/prisma/{search_id}",
        params={"study_type": "interventional"},
    )
    unfiltered = unfiltered_response.json()
    filtered = filtered_response.json()

    assert filtered_response.status_code == 200
    _assert_counts_shape(filtered)
    assert filtered["excluded"] >= unfiltered["excluded"]


@pytest.mark.asyncio
async def test_prisma_counts_with_age_group_filter(integration_client: AsyncClient, run_search) -> None:
    search_id, response = await run_search(query="metformin cardiovascular")
    assert response.status_code == 200

    unfiltered_response = await integration_client.get(f"/api/v1/prisma/{search_id}")
    filtered_response = await integration_client.get(
        f"/api/v1/prisma/{search_id}",
        params={"age_group": "adult"},
    )
    unfiltered = unfiltered_response.json()
    filtered = filtered_response.json()

    assert filtered_response.status_code == 200
    _assert_counts_shape(filtered)
    assert filtered["excluded"] >= unfiltered["excluded"]


@pytest.mark.asyncio
async def test_prisma_invalid_age_group_returns_422(integration_client: AsyncClient, run_search) -> None:
    search_id, response = await run_search(query="metformin cardiovascular")
    assert response.status_code == 200

    filtered_response = await integration_client.get(
        f"/api/v1/prisma/{search_id}",
        params={"age_group": "teenager"},
    )

    assert filtered_response.status_code == 422

