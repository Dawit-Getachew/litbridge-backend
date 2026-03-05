"""Tests for query preview endpoint translations."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from src.schemas.enums import SourceType


@pytest.mark.asyncio
async def test_preview_free_query_returns_translations_for_all_sources(
    async_client: AsyncClient,
) -> None:
    response = await async_client.post(
        "/api/v1/search/preview",
        json={"query": "metformin cardiovascular outcomes"},
    )
    body = response.json()

    assert response.status_code == 200
    assert "translations" in body
    assert set(body["translations"].keys()) == {source.value for source in SourceType}
    assert all(isinstance(value, str) and value.strip() for value in body["translations"].values())


@pytest.mark.asyncio
async def test_preview_selected_sources_only_returns_selected_translations(
    async_client: AsyncClient,
) -> None:
    response = await async_client.post(
        "/api/v1/search/preview",
        json={
            "query": "metformin cardiovascular outcomes",
            "sources": ["pubmed", "openalex"],
        },
    )
    body = response.json()

    assert response.status_code == 200
    assert set(body["translations"].keys()) == {"pubmed", "openalex"}


@pytest.mark.asyncio
async def test_preview_structured_query_uses_pico_components(
    async_client: AsyncClient,
) -> None:
    response = await async_client.post(
        "/api/v1/search/preview",
        json={
            "query": "heart failure metformin placebo mortality",
            "query_type": "structured",
            "sources": ["pubmed"],
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
    assert "translations" in body
    assert "pubmed" in body["translations"]
    assert "AND" in body["translations"]["pubmed"]


@pytest.mark.asyncio
async def test_preview_invalid_payload_returns_422(async_client: AsyncClient) -> None:
    response = await async_client.post(
        "/api/v1/search/preview",
        json={"query": "metformin outcomes", "sources": []},
    )

    assert response.status_code == 422
