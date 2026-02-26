"""Shared pytest fixtures for LitBridge API tests."""

from collections.abc import AsyncGenerator, Generator
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.core import deps
from src.core.config import get_settings


class MockAsyncSession:
    """Minimal async session mock for lightweight endpoint tests."""

    async def execute(self, *_args, **_kwargs) -> int:
        return 1


@pytest.fixture(autouse=True)
def override_test_settings(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Set deterministic test settings and refresh cached config."""

    monkeypatch.setenv("APP_NAME", "LitBridge Test")
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "8000")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/1")
    monkeypatch.setenv("NCBI_API_KEY", "test-key")
    monkeypatch.setenv("CONTACT_EMAIL", "test@example.com")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter")
    monkeypatch.setenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "")
    monkeypatch.setenv("CORS_ORIGINS", "[\"http://testserver\"]")
    monkeypatch.setenv("SECRET_KEY", "test-secret")

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    """Yield an async HTTP client with dependency overrides."""

    from src.main import app

    async def override_get_db() -> AsyncGenerator[MockAsyncSession, None]:
        yield MockAsyncSession()

    mock_redis = AsyncMock()
    mock_redis.ping = AsyncMock(return_value=True)

    def override_get_redis() -> AsyncMock:
        return mock_redis

    app.dependency_overrides[deps.get_db] = override_get_db
    app.dependency_overrides[deps.get_redis] = override_get_redis

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
