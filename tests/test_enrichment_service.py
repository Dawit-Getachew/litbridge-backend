"""Tests for semantic enrichment service behavior."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest

from src.schemas.enrichment import EnrichmentResponse
from src.schemas.enums import OAStatus, SearchMode, SourceType
from src.schemas.records import UnifiedRecord
from src.services.enrichment_service import EnrichmentService


class FakeRedis:
    """In-memory async Redis stub for enrichment caching tests."""

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


def _build_record(record_id: str, abstract: str | None = "Sample abstract") -> UnifiedRecord:
    return UnifiedRecord(
        id=record_id,
        title=f"Paper {record_id}",
        authors=["Author One"],
        source=SourceType.PUBMED,
        abstract=abstract,
        doi=f"10.1000/{record_id}",
        pmid=None,
        oa_status=OAStatus.OPEN,
        pdf_url="https://example.org/paper.pdf",
    )


@pytest.mark.asyncio
async def test_enrich_record_with_s2_tldr_returns_semantic_scholar_summary() -> None:
    """If S2 has TLDR metadata, service should use it without LLM fallback."""
    fake_redis = FakeRedis()
    s2_repo = AsyncMock()
    llm_client = AsyncMock()
    s2_repo.get_paper.return_value = {"tldr": {"text": "S2 summary."}, "citationCount": 17}

    service = EnrichmentService(s2_repo=s2_repo, llm_client=llm_client, redis_client=fake_redis)
    response = await service.enrich_record(_build_record("r1"))

    assert response.id == "r1"
    assert response.tldr == "S2 summary."
    assert response.citation_count == 17
    llm_client.generate_tldr.assert_not_awaited()


@pytest.mark.asyncio
async def test_enrich_record_with_s2_unavailable_falls_back_to_llm() -> None:
    """When S2 is missing, abstract-based LLM TLDR should be used."""
    fake_redis = FakeRedis()
    s2_repo = AsyncMock()
    llm_client = AsyncMock()
    s2_repo.get_paper.return_value = None
    llm_client.generate_tldr.return_value = "LLM fallback summary."

    service = EnrichmentService(s2_repo=s2_repo, llm_client=llm_client, redis_client=fake_redis)
    response = await service.enrich_record(_build_record("r2"))

    assert response.tldr == "LLM fallback summary."
    assert response.citation_count is None
    llm_client.generate_tldr.assert_awaited_once()


@pytest.mark.asyncio
async def test_enrich_record_no_abstract_and_no_s2_returns_partial() -> None:
    """Without S2 metadata and abstract, response should be partial and valid."""
    fake_redis = FakeRedis()
    s2_repo = AsyncMock()
    llm_client = AsyncMock()
    s2_repo.get_paper.return_value = None

    service = EnrichmentService(s2_repo=s2_repo, llm_client=llm_client, redis_client=fake_redis)
    response = await service.enrich_record(_build_record("r3", abstract=None))

    assert response.id == "r3"
    assert response.tldr is None
    assert response.citation_count is None
    llm_client.generate_tldr.assert_not_awaited()


@pytest.mark.asyncio
async def test_enrich_batch_processes_multiple_records_concurrently(monkeypatch: pytest.MonkeyPatch) -> None:
    """Batch enrichment should run multiple records concurrently via semaphore."""
    fake_redis = FakeRedis()
    service = EnrichmentService(s2_repo=AsyncMock(), llm_client=AsyncMock(), redis_client=fake_redis)
    records = [_build_record(f"b{i}") for i in range(6)]

    active = 0
    max_active = 0

    async def fake_enrich(record: UnifiedRecord) -> EnrichmentResponse:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        return EnrichmentResponse(id=record.id)

    monkeypatch.setattr(service, "enrich_record", fake_enrich)
    responses = await service.enrich_batch(records)

    assert len(responses) == len(records)
    assert max_active > 1
    assert max_active <= service.BATCH_CONCURRENCY


@pytest.mark.asyncio
async def test_enrich_record_caching_returns_cached_on_second_call() -> None:
    """Second enrichment call should hit cache and skip upstream lookups."""
    fake_redis = FakeRedis()
    s2_repo = AsyncMock()
    llm_client = AsyncMock()
    s2_repo.get_paper.return_value = {"tldr": {"text": "Cached S2 summary."}, "citationCount": 9}

    service = EnrichmentService(s2_repo=s2_repo, llm_client=llm_client, redis_client=fake_redis)
    record = _build_record("cache-1")

    first = await service.enrich_record(record)
    second = await service.enrich_record(record)

    assert first == second
    assert fake_redis.set_calls == 1
    assert s2_repo.get_paper.await_count == 1
    llm_client.generate_tldr.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_thinking_deep_mode_streams_multiple_chunks() -> None:
    """DEEP_THINKING mode should forward streaming analysis chunks."""
    fake_redis = FakeRedis()
    s2_repo = AsyncMock()
    llm_client = AsyncMock()

    async def fake_stream_analysis(query: str, records: list[UnifiedRecord]) -> AsyncGenerator[str, None]:
        _ = (query, records)
        yield "chunk-1 "
        yield "chunk-2"

    llm_client.stream_analysis = fake_stream_analysis
    llm_client.quick_summary = AsyncMock(return_value="unused")

    service = EnrichmentService(s2_repo=s2_repo, llm_client=llm_client, redis_client=fake_redis)
    chunks = [
        chunk
        async for chunk in service.stream_thinking(
            query="covid treatment",
            records=[_build_record("s1")],
            mode=SearchMode.DEEP_THINKING,
        )
    ]

    assert chunks == ["chunk-1 ", "chunk-2"]


@pytest.mark.asyncio
async def test_stream_thinking_light_mode_streams_tokens() -> None:
    """LIGHT_THINKING mode should stream tokens via stream_quick_summary."""
    fake_redis = FakeRedis()
    s2_repo = AsyncMock()
    llm_client = AsyncMock()

    async def fake_stream_quick_summary(query: str, records: list[UnifiedRecord]) -> AsyncGenerator[str, None]:
        _ = (query, records)
        yield "Short "
        yield "synthesized "
        yield "summary."

    llm_client.stream_quick_summary = fake_stream_quick_summary

    service = EnrichmentService(s2_repo=s2_repo, llm_client=llm_client, redis_client=fake_redis)
    chunks = [
        chunk
        async for chunk in service.stream_thinking(
            query="hypertension",
            records=[_build_record("s2")],
            mode=SearchMode.LIGHT_THINKING,
        )
    ]

    assert chunks == ["Short ", "synthesized ", "summary."]


@pytest.mark.asyncio
async def test_enrich_record_llm_failure_returns_partial_response() -> None:
    """LLM failures should be swallowed and still return partial enrichment."""
    fake_redis = FakeRedis()
    s2_repo = AsyncMock()
    llm_client = AsyncMock()
    s2_repo.get_paper.return_value = None
    llm_client.generate_tldr.side_effect = RuntimeError("llm unavailable")

    service = EnrichmentService(s2_repo=s2_repo, llm_client=llm_client, redis_client=fake_redis)
    response = await service.enrich_record(_build_record("r4"))

    assert response.id == "r4"
    assert response.tldr is None
    assert response.citation_count is None
