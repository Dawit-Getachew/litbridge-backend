"""Tests for federated fetch orchestrator service."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from src.core.config import get_settings
from src.schemas.enums import OAStatus, QueryType, SearchMode, SourceType
from src.schemas.records import RawRecord
from src.services.fetcher_service import FetcherService


class FakeRedis:
    """In-memory async Redis stub for cache behavior tests."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.get_calls = 0
        self.set_calls = 0
        self.last_ttl: int | None = None

    async def get(self, key: str) -> bytes | None:
        self.get_calls += 1
        return self.store.get(key)

    async def set(self, key: str, value: bytes, ex: int | None = None) -> bool:
        self.set_calls += 1
        self.last_ttl = ex
        self.store[key] = value
        return True


class StubRepository:
    """Repository stub exposing an async search mock."""

    def __init__(self, result: list[RawRecord] | Exception) -> None:
        if isinstance(result, Exception):
            self.search = AsyncMock(side_effect=result)
        else:
            self.search = AsyncMock(return_value=result)


def _build_raw_record(source: SourceType, suffix: str) -> RawRecord:
    return RawRecord(
        source_id=f"{source.value}-{suffix}",
        source=source,
        title=f"Sample title {suffix}",
        authors=["Author A"],
        oa_status=OAStatus.UNKNOWN,
    )


@pytest.mark.asyncio
async def test_fetch_all_sources_returns_combined_results(monkeypatch: pytest.MonkeyPatch) -> None:
    """All requested sources should be fetched and merged."""
    sources = [
        SourceType.PUBMED,
        SourceType.EUROPEPMC,
        SourceType.OPENALEX,
        SourceType.CLINICALTRIALS,
    ]
    translated = {source: f"{source.value} translated" for source in sources}
    repositories = {
        source: StubRepository([_build_raw_record(source, "1")]) for source in sources
    }
    translator = AsyncMock(return_value=translated)
    fake_redis = FakeRedis()

    monkeypatch.setattr("src.services.fetcher_service.translate_for_all_sources", translator)
    monkeypatch.setattr("src.services.fetcher_service.get_repository", lambda source, client: repositories[source])

    async with httpx.AsyncClient() as client:
        service = FetcherService(client=client, redis_client=fake_redis, settings=get_settings())
        records, source_counts, failed_sources = await service.fetch_all_sources(
            query="heart disease",
            query_type=QueryType.FREE,
            search_mode=SearchMode.DEEP_RESEARCH,
            sources=sources,
            max_results=100,
        )

    assert len(records) == 4
    assert source_counts == {source: 1 for source in sources}
    assert failed_sources == []
    assert fake_redis.set_calls == 1
    assert fake_redis.last_ttl == FetcherService.CACHE_TTL_SECONDS
    translator.assert_awaited_once()
    for source in sources:
        repositories[source].search.assert_awaited_once_with(
            query=f"{source.value} translated",
            max_results=100,
        )


@pytest.mark.asyncio
async def test_fetch_all_sources_returns_partial_results_when_one_source_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing source should not block other sources."""
    sources = [SourceType.PUBMED, SourceType.OPENALEX]
    translated = {source: f"{source.value} translated" for source in sources}
    repositories = {
        SourceType.PUBMED: StubRepository([_build_raw_record(SourceType.PUBMED, "ok")]),
        SourceType.OPENALEX: StubRepository(RuntimeError("openalex failure")),
    }
    translator = AsyncMock(return_value=translated)
    fake_redis = FakeRedis()

    monkeypatch.setattr("src.services.fetcher_service.translate_for_all_sources", translator)
    monkeypatch.setattr("src.services.fetcher_service.get_repository", lambda source, client: repositories[source])

    async with httpx.AsyncClient() as client:
        service = FetcherService(client=client, redis_client=fake_redis, settings=get_settings())
        records, source_counts, failed_sources = await service.fetch_all_sources(
            query="metformin",
            query_type=QueryType.FREE,
            search_mode=SearchMode.DEEP_RESEARCH,
            sources=sources,
            max_results=80,
        )

    assert len(records) == 1
    assert records[0].source is SourceType.PUBMED
    assert source_counts == {SourceType.PUBMED: 1, SourceType.OPENALEX: 0}
    assert failed_sources == [SourceType.OPENALEX]


@pytest.mark.asyncio
async def test_quick_mode_caps_results_per_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """QUICK mode should cap per-source max_results at 50."""
    source = SourceType.OPENALEX
    translator = AsyncMock(return_value={source: "translated query"})
    repository = StubRepository([])
    fake_redis = FakeRedis()

    monkeypatch.setattr("src.services.fetcher_service.translate_for_all_sources", translator)
    monkeypatch.setattr("src.services.fetcher_service.get_repository", lambda source, client: repository)

    async with httpx.AsyncClient() as client:
        service = FetcherService(client=client, redis_client=fake_redis, settings=get_settings())
        await service.fetch_all_sources(
            query="oncology",
            query_type=QueryType.FREE,
            search_mode=SearchMode.QUICK,
            sources=[source],
            max_results=500,
        )

    repository.search.assert_awaited_once_with(query="translated query", max_results=50)


@pytest.mark.asyncio
async def test_deep_research_mode_allows_full_results(monkeypatch: pytest.MonkeyPatch) -> None:
    """DEEP_RESEARCH mode should preserve requested max_results."""
    source = SourceType.EUROPEPMC
    translator = AsyncMock(return_value={source: "translated query"})
    repository = StubRepository([])
    fake_redis = FakeRedis()

    monkeypatch.setattr("src.services.fetcher_service.translate_for_all_sources", translator)
    monkeypatch.setattr("src.services.fetcher_service.get_repository", lambda source, client: repository)

    async with httpx.AsyncClient() as client:
        service = FetcherService(client=client, redis_client=fake_redis, settings=get_settings())
        await service.fetch_all_sources(
            query="longitudinal study",
            query_type=QueryType.FREE,
            search_mode=SearchMode.DEEP_RESEARCH,
            sources=[source],
            max_results=140,
        )

    repository.search.assert_awaited_once_with(query="translated query", max_results=140)


@pytest.mark.asyncio
async def test_fetch_all_sources_hits_cache_on_second_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Second identical request should return cached payload without refetching."""
    sources = [SourceType.PUBMED, SourceType.CLINICALTRIALS]
    translated = {source: f"{source.value} translated" for source in sources}
    repositories = {
        SourceType.PUBMED: StubRepository([_build_raw_record(SourceType.PUBMED, "a")]),
        SourceType.CLINICALTRIALS: StubRepository([_build_raw_record(SourceType.CLINICALTRIALS, "b")]),
    }
    translator = AsyncMock(return_value=translated)
    fake_redis = FakeRedis()

    monkeypatch.setattr("src.services.fetcher_service.translate_for_all_sources", translator)
    monkeypatch.setattr("src.services.fetcher_service.get_repository", lambda source, client: repositories[source])

    async with httpx.AsyncClient() as client:
        service = FetcherService(client=client, redis_client=fake_redis, settings=get_settings())
        first_result = await service.fetch_all_sources(
            query="cached query",
            query_type=QueryType.FREE,
            search_mode=SearchMode.DEEP_RESEARCH,
            sources=sources,
            max_results=60,
        )
        second_result = await service.fetch_all_sources(
            query="cached query",
            query_type=QueryType.FREE,
            search_mode=SearchMode.DEEP_RESEARCH,
            sources=sources,
            max_results=60,
        )

    assert first_result == second_result
    assert fake_redis.set_calls == 1
    assert fake_redis.get_calls >= 2
    translator.assert_awaited_once()
    assert repositories[SourceType.PUBMED].search.await_count == 1
    assert repositories[SourceType.CLINICALTRIALS].search.await_count == 1


@pytest.mark.asyncio
async def test_fetch_all_sources_raises_for_empty_sources() -> None:
    """Empty source selection should fail fast with validation error."""
    fake_redis = FakeRedis()
    async with httpx.AsyncClient() as client:
        service = FetcherService(client=client, redis_client=fake_redis, settings=get_settings())
        with pytest.raises(ValueError, match="At least one source must be provided"):
            await service.fetch_all_sources(
                query="any query",
                query_type=QueryType.FREE,
                search_mode=SearchMode.QUICK,
                sources=[],
                max_results=10,
            )
