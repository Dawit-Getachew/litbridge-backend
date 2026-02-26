"""Dependency providers for database, cache, HTTP, and settings."""

from collections.abc import AsyncGenerator

import httpx
import redis.asyncio as redis
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings, get_settings as get_cached_settings
from src.core.database import get_db_session
from src.core.redis import get_redis as get_redis_client
from src.services import FetcherService


def get_settings() -> Settings:
    """Return cached application settings."""

    return get_cached_settings()


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """Yield a database session from the application session factory."""

    session_factory = request.app.state.db_session_factory
    async for session in get_db_session(session_factory):
        yield session


def get_redis(_request: Request) -> redis.Redis:
    """Return the shared Redis client."""

    return get_redis_client()


def get_http_client(request: Request) -> httpx.AsyncClient:
    """Return the shared HTTP client from app state."""

    return request.app.state.http_client


def get_fetcher_service(request: Request) -> FetcherService:
    """Return the federated fetch orchestrator service."""

    return FetcherService(
        client=get_http_client(request),
        redis_client=get_redis(request),
        settings=get_settings(),
    )


def get_search_repo() -> None:
    """Placeholder dependency for search repository wiring."""

    return None
