"""Integration tests for database and Redis infrastructure."""

from __future__ import annotations

import os
import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from src.core.config import get_settings
from src.core.database import create_engine, create_session_factory
from src.core.redis import build_cache_key, cache_delete, cache_get, cache_set, create_redis_pool
from src.models import Base, SearchSession


@pytest.fixture
def integration_settings(override_test_settings: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Override DB/cache settings for Docker-backed integration tests."""

    database_url = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://litbridge:litbridge_dev_2026@localhost:5432/litbridge",
    )
    redis_url = os.getenv("TEST_REDIS_URL", "redis://localhost:6379/1")

    monkeypatch.setenv(
        "DATABASE_URL",
        database_url,
    )
    monkeypatch.setenv("REDIS_URL", redis_url)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def db_engine(integration_settings: None) -> AsyncEngine:
    """Create the async engine and ensure tables exist for tests."""

    engine = create_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE TABLE search_sessions RESTART IDENTITY CASCADE"))
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncSession:
    """Yield an async DB session."""

    session_factory = create_session_factory(db_engine)
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def redis_client(integration_settings: None):
    """Yield a clean Redis client for cache tests."""

    client = create_redis_pool()
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.mark.asyncio
async def test_async_engine_connects(db_engine: AsyncEngine) -> None:
    """Async engine should execute a basic SQL query."""

    async with db_engine.connect() as connection:
        result = await connection.execute(text("SELECT 1"))
    assert result.scalar_one() == 1


@pytest.mark.asyncio
async def test_search_session_create_and_query(db_session: AsyncSession) -> None:
    """SearchSession should persist and load with JSON payload fields."""

    session = SearchSession(
        query="hypertension treatment",
        query_type="free",
        search_mode="quick",
        sources=["pubmed", "openalex"],
        pico=None,
        status="completed",
        total_identified=20,
        total_after_dedup=15,
        results=[{"title": "Sample paper"}],
        sources_completed=["pubmed", "openalex"],
        sources_failed=[],
    )
    db_session.add(session)
    await db_session.commit()

    stmt = select(SearchSession).where(SearchSession.id == session.id)
    fetched = (await db_session.execute(stmt)).scalar_one()

    assert fetched.query == "hypertension treatment"
    assert fetched.total_identified == 20
    assert fetched.total_after_dedup == 15
    assert fetched.results == [{"title": "Sample paper"}]


@pytest.mark.asyncio
async def test_redis_cache_round_trip(redis_client) -> None:
    """Cache set/get should round-trip byte payloads."""

    _ = redis_client
    key = build_cache_key("search", "test-search-id:results")
    payload = b'{"status":"ok"}'

    await cache_set(key, payload, ttl=60)
    cached = await cache_get(key)

    assert cached == payload

    await cache_delete(key)
    assert await cache_get(key) is None
