"""Federated fetch orchestrator service for the fast search path."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time

import httpx
import structlog
from redis import RedisError
from redis.asyncio import Redis

from src.ai.adapters import translate_for_all_sources
from src.core.config import Settings
from src.repositories import get_repository
from src.schemas.enums import QueryType, SearchMode, SourceType
from src.schemas.pico import PICOInput
from src.schemas.records import RawRecord


class FetcherService:
    """Run query translation and federated source fetches in parallel."""

    CACHE_TTL_SECONDS = 60 * 60 * 24  # 24 hours

    def __init__(self, client: httpx.AsyncClient, redis_client: Redis, settings: Settings) -> None:
        self.client = client
        self.redis_client = redis_client
        self.settings = settings
        self.logger = structlog.get_logger(__name__).bind(service="fetcher_service")

    async def fetch_all_sources(
        self,
        query: str,
        query_type: QueryType,
        search_mode: SearchMode,
        sources: list[SourceType],
        pico: PICOInput | None = None,
        max_results: int = 100,
    ) -> tuple[list[RawRecord], dict[SourceType, int], list[SourceType]]:
        """Fetch records from all requested sources concurrently."""
        if not sources:
            raise ValueError("At least one source must be provided.")

        effective_max_results = self._max_results_for_mode(search_mode, max_results)
        if effective_max_results <= 0:
            return [], {source: 0 for source in sources}, []

        cache_key = self._build_cache_key(query=query, sources=sources, mode=search_mode)
        cached_payload = await self._cache_get(cache_key)
        if cached_payload is not None:
            self.logger.info(
                "fetcher_cache_hit",
                cache_key=cache_key,
                source_count=len(sources),
                total_records=len(cached_payload[0]),
            )
            return cached_payload

        self.logger.info(
            "fetcher_cache_miss",
            cache_key=cache_key,
            source_count=len(sources),
            search_mode=search_mode.value,
            max_results=effective_max_results,
        )

        translated_queries = await translate_for_all_sources(
            query=query,
            query_type=query_type,
            pico=pico,
            sources=sources,
        )

        started_at = time.perf_counter()
        source_results = await asyncio.gather(
            *[
                self._fetch_source(
                    source=source,
                    translated_query=translated_queries.get(source, query),
                    max_results=effective_max_results,
                )
                for source in sources
            ],
            return_exceptions=True,
        )

        all_records: list[RawRecord] = []
        source_counts: dict[SourceType, int] = {source: 0 for source in sources}
        failed_sources: list[SourceType] = []

        for source, result in zip(sources, source_results, strict=True):
            if isinstance(result, Exception):
                failed_sources.append(source)
                continue
            source_counts[source] = len(result)
            all_records.extend(result)

        total_duration_ms = int((time.perf_counter() - started_at) * 1000)
        self.logger.info(
            "fetcher_completed",
            source_count=len(sources),
            failed_count=len(failed_sources),
            total_records=len(all_records),
            duration_ms=total_duration_ms,
        )

        await self._cache_set(
            cache_key=cache_key,
            records=all_records,
            source_counts=source_counts,
            failed_sources=failed_sources,
        )

        return all_records, source_counts, failed_sources

    async def _fetch_source(self, source: SourceType, translated_query: str, max_results: int) -> list[RawRecord]:
        """Fetch one source and emit structured timing logs."""
        repository = get_repository(source=source, client=self.client)
        started_at = time.perf_counter()
        try:
            records = await repository.search(query=translated_query, max_results=max_results)
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            self.logger.warning(
                "fetcher_source_failed",
                source=source.value,
                duration_ms=duration_ms,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise

        duration_ms = int((time.perf_counter() - started_at) * 1000)
        self.logger.info(
            "fetcher_source_completed",
            source=source.value,
            count=len(records),
            duration_ms=duration_ms,
        )
        return records

    @staticmethod
    def _max_results_for_mode(mode: SearchMode, requested: int) -> int:
        """Compute effective per-source max results based on search mode."""
        normalized_requested = max(requested, 0)
        if mode is SearchMode.QUICK:
            return min(normalized_requested, 50)
        if mode is SearchMode.LIGHT_THINKING:
            return min(normalized_requested, 30)
        return normalized_requested

    @staticmethod
    def _build_cache_key(query: str, sources: list[SourceType], mode: SearchMode) -> str:
        """Build a deterministic key: litbridge:search:{hash}:raw."""
        payload = {
            "query": query,
            "sources": sorted(source.value for source in sources),
            "mode": mode.value,
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
        return f"litbridge:search:{digest}:raw"

    async def _cache_get(
        self,
        cache_key: str,
    ) -> tuple[list[RawRecord], dict[SourceType, int], list[SourceType]] | None:
        """Read and decode fetch payload from Redis."""
        try:
            cached_value = await self.redis_client.get(cache_key)
        except RedisError:
            self.logger.warning("fetcher_cache_read_failed", cache_key=cache_key)
            return None

        if cached_value is None:
            return None

        if isinstance(cached_value, bytes):
            payload_raw = cached_value.decode("utf-8")
        elif isinstance(cached_value, str):
            payload_raw = cached_value
        else:
            self.logger.warning(
                "fetcher_cache_invalid_type",
                cache_key=cache_key,
                value_type=type(cached_value).__name__,
            )
            return None

        try:
            payload = json.loads(payload_raw)
            records = [RawRecord.model_validate(item) for item in payload.get("records", [])]
            source_counts = {
                SourceType(source): int(count)
                for source, count in payload.get("source_counts", {}).items()
            }
            failed_sources = [SourceType(source) for source in payload.get("failed_sources", [])]
        except (json.JSONDecodeError, TypeError, ValueError):
            self.logger.warning("fetcher_cache_decode_failed", cache_key=cache_key)
            return None

        return records, source_counts, failed_sources

    async def _cache_set(
        self,
        cache_key: str,
        records: list[RawRecord],
        source_counts: dict[SourceType, int],
        failed_sources: list[SourceType],
    ) -> None:
        """Encode and write fetch payload to Redis."""
        payload = {
            "records": [record.model_dump(mode="json") for record in records],
            "source_counts": {source.value: count for source, count in source_counts.items()},
            "failed_sources": [source.value for source in failed_sources],
        }

        try:
            await self.redis_client.set(
                cache_key,
                json.dumps(payload).encode("utf-8"),
                ex=self.CACHE_TTL_SECONDS,
            )
        except RedisError:
            self.logger.warning("fetcher_cache_write_failed", cache_key=cache_key)
