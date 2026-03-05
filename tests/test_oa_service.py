"""Tests for OA resolver cascade and caching behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.schemas.enums import OAStatus, SourceType
from src.schemas.records import UnifiedRecord
from src.services.oa_service import OAService


class FakeRedis:
    """In-memory Redis stub for OA cache behavior tests."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.set_calls = 0
        self.last_ttl: int | None = None

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    async def set(self, key: str, value: bytes, ex: int | None = None) -> bool:
        self.set_calls += 1
        self.last_ttl = ex
        self.store[key] = value
        return True


def _build_record(
    record_id: str,
    *,
    doi: str | None = None,
    pmid: str | None = None,
    oa_status: OAStatus = OAStatus.UNKNOWN,
    pdf_url: str | None = None,
) -> UnifiedRecord:
    return UnifiedRecord(
        id=record_id,
        title=f"Paper {record_id}",
        authors=["Author One"],
        source=SourceType.OPENALEX,
        doi=doi,
        pmid=pmid,
        oa_status=oa_status,
        pdf_url=pdf_url,
    )


def _build_service(fake_redis: FakeRedis) -> tuple[OAService, AsyncMock, AsyncMock, AsyncMock]:
    openalex_repo = AsyncMock()
    unpaywall_repo = AsyncMock()
    europepmc_repo = AsyncMock()
    service = OAService(
        openalex_repo=openalex_repo,
        unpaywall_repo=unpaywall_repo,
        europepmc_repo=europepmc_repo,
        redis_client=fake_redis,
    )
    return service, openalex_repo, unpaywall_repo, europepmc_repo


@pytest.mark.asyncio
async def test_resolve_oa_existing_openalex_pdf_stops_cascade() -> None:
    """If record already has OA PDF, resolver should return immediately."""
    fake_redis = FakeRedis()
    service, _openalex_repo, unpaywall_repo, europepmc_repo = _build_service(fake_redis)

    record = _build_record(
        "oa-1",
        doi="10.1000/oa-1",
        oa_status=OAStatus.OPEN,
        pdf_url="https://openalex.org/oa.pdf",
    )
    status, url = await service.resolve_oa(record)

    assert status is OAStatus.OPEN
    assert url == "https://openalex.org/oa.pdf"
    unpaywall_repo.get_oa_url.assert_not_awaited()
    europepmc_repo.get_fulltext_url.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_oa_openalex_miss_unpaywall_hit_returns_pdf() -> None:
    """When OpenAlex fields miss, resolver should use Unpaywall PDF if found."""
    fake_redis = FakeRedis()
    service, _openalex_repo, unpaywall_repo, europepmc_repo = _build_service(fake_redis)
    unpaywall_repo.get_oa_url.return_value = (OAStatus.OPEN, "https://unpaywall.org/oa.pdf")

    record = _build_record("oa-2", doi="10.1000/oa-2", oa_status=OAStatus.UNKNOWN, pdf_url=None)
    status, url = await service.resolve_oa(record)

    assert status is OAStatus.OPEN
    assert url == "https://unpaywall.org/oa.pdf"
    unpaywall_repo.get_oa_url.assert_awaited_once_with("10.1000/oa-2")
    europepmc_repo.get_fulltext_url.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_oa_unpaywall_404_then_europepmc_hit_returns_url() -> None:
    """If Unpaywall has no OA, resolver should fall back to Europe PMC full text."""
    fake_redis = FakeRedis()
    service, _openalex_repo, unpaywall_repo, europepmc_repo = _build_service(fake_redis)
    unpaywall_repo.get_oa_url.return_value = (OAStatus.CLOSED, None)
    europepmc_repo.get_fulltext_url.return_value = "https://europepmc.org/webservices/rest/123/fullTextXML"

    record = _build_record("oa-3", doi="10.1000/oa-3", pmid="123")
    status, url = await service.resolve_oa(record)

    assert status is OAStatus.OPEN
    assert url == "https://europepmc.org/webservices/rest/123/fullTextXML"
    unpaywall_repo.get_oa_url.assert_awaited_once_with("10.1000/oa-3")
    europepmc_repo.get_fulltext_url.assert_awaited_once_with("123")


@pytest.mark.asyncio
async def test_resolve_oa_all_sources_fail_returns_closed() -> None:
    """If all OA sources fail, resolver should return CLOSED with no URL."""
    fake_redis = FakeRedis()
    service, _openalex_repo, unpaywall_repo, europepmc_repo = _build_service(fake_redis)
    unpaywall_repo.get_oa_url.return_value = (OAStatus.CLOSED, None)
    europepmc_repo.get_fulltext_url.return_value = None

    record = _build_record("oa-4", doi="10.1000/oa-4", pmid="999")
    status, url = await service.resolve_oa(record)

    assert status is OAStatus.CLOSED
    assert url is None


@pytest.mark.asyncio
async def test_resolve_batch_returns_mixed_results() -> None:
    """Batch resolve should return OA results for each record id."""
    fake_redis = FakeRedis()
    service, _openalex_repo, unpaywall_repo, europepmc_repo = _build_service(fake_redis)

    unpaywall_repo.get_oa_url.side_effect = [
        (OAStatus.OPEN, "https://unpaywall.org/oa-b.pdf"),
        (OAStatus.CLOSED, None),
    ]
    europepmc_repo.get_fulltext_url.return_value = None

    records = [
        _build_record("batch-1", doi="10.1000/batch-1", oa_status=OAStatus.OPEN, pdf_url="https://openalex.org/a.pdf"),
        _build_record("batch-2", doi="10.1000/batch-2"),
        _build_record("batch-3", doi="10.1000/batch-3", pmid="303"),
    ]

    results = await service.resolve_batch(records)

    assert results["batch-1"] == (OAStatus.OPEN, "https://openalex.org/a.pdf")
    assert results["batch-2"] == (OAStatus.OPEN, "https://unpaywall.org/oa-b.pdf")
    assert results["batch-3"] == (OAStatus.CLOSED, None)


@pytest.mark.asyncio
async def test_resolve_oa_second_call_uses_cache() -> None:
    """Second OA resolve should read from cache and skip external calls."""
    fake_redis = FakeRedis()
    service, _openalex_repo, unpaywall_repo, europepmc_repo = _build_service(fake_redis)
    unpaywall_repo.get_oa_url.return_value = (OAStatus.OPEN, "https://unpaywall.org/cache.pdf")
    europepmc_repo.get_fulltext_url.return_value = None

    record = _build_record("cache-1", doi="10.1000/cache-1")

    first = await service.resolve_oa(record)
    second = await service.resolve_oa(record)

    assert first == (OAStatus.OPEN, "https://unpaywall.org/cache.pdf")
    assert second == first
    assert fake_redis.set_calls == 1
    unpaywall_repo.get_oa_url.assert_awaited_once_with("10.1000/cache-1")


@pytest.mark.asyncio
async def test_resolve_oa_without_doi_or_pmid_returns_unknown() -> None:
    """Records lacking DOI and PMID should remain UNKNOWN with no URL."""
    fake_redis = FakeRedis()
    service, _openalex_repo, unpaywall_repo, europepmc_repo = _build_service(fake_redis)

    record = _build_record("oa-unknown")
    status, url = await service.resolve_oa(record)

    assert status is OAStatus.UNKNOWN
    assert url is None
    unpaywall_repo.get_oa_url.assert_not_awaited()
    europepmc_repo.get_fulltext_url.assert_not_awaited()
