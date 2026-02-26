"""Redis client setup and cache helper utilities."""

from __future__ import annotations

from redis import RedisError
from redis.asyncio import Redis

from src.core.config import get_settings

_redis_client: Redis | None = None


def create_redis_pool() -> Redis:
    """Create and store the shared async Redis client."""

    global _redis_client
    settings = get_settings()
    _redis_client = Redis.from_url(
        settings.REDIS_URL,
        decode_responses=False,
        max_connections=20,
    )
    return _redis_client


def get_redis() -> Redis:
    """Return the shared async Redis client."""

    if _redis_client is None:
        raise RuntimeError("Redis client has not been initialized. Call create_redis_pool() first.")
    return _redis_client


def build_cache_key(domain: str, identifier: str) -> str:
    """Build namespaced cache keys as litbridge:{domain}:{identifier}."""

    return f"litbridge:{domain}:{identifier}"


async def cache_get(key: str) -> bytes | None:
    """Get a cached value by key."""

    try:
        return await get_redis().get(key)
    except RedisError:
        return None


async def cache_set(key: str, value: bytes, ttl: int = 86400) -> None:
    """Set a cached value with TTL in seconds (24h default)."""

    try:
        await get_redis().set(key, value, ex=ttl)
    except RedisError:
        return None


async def cache_delete(key: str) -> None:
    """Delete a cached key if present."""

    try:
        await get_redis().delete(key)
    except RedisError:
        return None
