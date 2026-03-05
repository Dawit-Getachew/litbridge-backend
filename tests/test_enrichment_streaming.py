"""Tests for enrichment service streaming behavior."""

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
    """In-memory async Redis stub."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    async def set(self, key: str, value: bytes, ex: int | None = None) -> bool:
        self.store[key] = value
        return True


def _build_record(record_id: str) -> UnifiedRecord:
    return UnifiedRecord(
        id=record_id,
        title=f"Paper {record_id}",
        authors=["Author One"],
        source=SourceType.PUBMED,
        abstract="Sample abstract",
        doi=f"10.1000/{record_id}",
        oa_status=OAStatus.OPEN,
        pdf_url="https://example.org/paper.pdf",
    )


@pytest.mark.asyncio
async def test_enrich_stream_yields_results_as_each_completes() -> None:
    """enrich_stream should yield EnrichmentResponse objects one at a time."""
    fake_redis = FakeRedis()
    s2_repo = AsyncMock()
    llm_client = AsyncMock()
    s2_repo.get_paper.return_value = {"tldr": {"text": "Summary."}, "citationCount": 5}

    service = EnrichmentService(s2_repo=s2_repo, llm_client=llm_client, redis_client=fake_redis)
    records = [_build_record(f"stream-{i}") for i in range(4)]

    results = []
    async for response in service.enrich_stream(records):
        results.append(response)
        assert isinstance(response, EnrichmentResponse)

    assert len(results) == len(records)
    assert all(r.tldr == "Summary." for r in results)


@pytest.mark.asyncio
async def test_enrich_stream_empty_list() -> None:
    """enrich_stream with no records should yield nothing."""
    fake_redis = FakeRedis()
    service = EnrichmentService(s2_repo=AsyncMock(), llm_client=AsyncMock(), redis_client=fake_redis)

    results = [r async for r in service.enrich_stream([])]
    assert results == []


@pytest.mark.asyncio
async def test_enrich_stream_tolerates_individual_failures() -> None:
    """If one record fails enrichment, stream should continue with others."""
    fake_redis = FakeRedis()
    s2_repo = AsyncMock()
    llm_client = AsyncMock()

    call_count = 0

    async def flaky_get_paper(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("S2 intermittent failure")
        return {"tldr": {"text": "OK."}, "citationCount": 1}

    s2_repo.get_paper = flaky_get_paper
    llm_client.generate_tldr.return_value = "Fallback."

    service = EnrichmentService(s2_repo=s2_repo, llm_client=llm_client, redis_client=fake_redis)
    records = [_build_record(f"flaky-{i}") for i in range(3)]

    results = [r async for r in service.enrich_stream(records)]
    assert len(results) == 3


@pytest.mark.asyncio
async def test_stream_thinking_light_mode_streams_tokens() -> None:
    """LIGHT_THINKING should now stream token-by-token via stream_quick_summary."""
    fake_redis = FakeRedis()
    s2_repo = AsyncMock()
    llm_client = AsyncMock()

    async def fake_stream_quick_summary(query: str, records: list[UnifiedRecord]) -> AsyncGenerator[str, None]:
        yield "Quick "
        yield "summary "
        yield "here."

    llm_client.stream_quick_summary = fake_stream_quick_summary

    service = EnrichmentService(s2_repo=s2_repo, llm_client=llm_client, redis_client=fake_redis)
    chunks = [
        chunk
        async for chunk in service.stream_thinking(
            query="test",
            records=[_build_record("lt1")],
            mode=SearchMode.LIGHT_THINKING,
        )
    ]

    assert chunks == ["Quick ", "summary ", "here."]


@pytest.mark.asyncio
async def test_stream_thinking_deep_mode_still_works() -> None:
    """DEEP_THINKING should continue to stream via stream_analysis."""
    fake_redis = FakeRedis()
    s2_repo = AsyncMock()
    llm_client = AsyncMock()

    async def fake_stream_analysis(query: str, records: list[UnifiedRecord]) -> AsyncGenerator[str, None]:
        yield "Deep "
        yield "analysis."

    llm_client.stream_analysis = fake_stream_analysis

    service = EnrichmentService(s2_repo=s2_repo, llm_client=llm_client, redis_client=fake_redis)
    chunks = [
        chunk
        async for chunk in service.stream_thinking(
            query="test",
            records=[_build_record("dt1")],
            mode=SearchMode.DEEP_THINKING,
        )
    ]

    assert chunks == ["Deep ", "analysis."]
