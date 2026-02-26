"""Health and root endpoint tests for LitBridge API."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_root_returns_ok(async_client: AsyncClient) -> None:
    """Root endpoint should return API metadata."""

    response = await async_client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "name": "LitBridge API",
        "version": "1.0.0",
        "status": "ok",
    }


@pytest.mark.asyncio
async def test_health_returns_ok(async_client: AsyncClient) -> None:
    """Health endpoint should report status for dependencies."""

    response = await async_client.get("/health")
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "ok"
    assert "database" in body
    assert "redis" in body
