"""Tests for PRISMA count computations."""

from __future__ import annotations

from src.schemas.enums import OAStatus, SourceType
from src.schemas.prisma import PrismaFilters
from src.schemas.records import UnifiedRecord
from src.services.prisma_service import PrismaService


def _build_unified_record(
    *,
    record_id: str,
    source: SourceType,
    year: int | None,
    oa_status: OAStatus,
    title: str,
) -> UnifiedRecord:
    return UnifiedRecord(
        id=record_id,
        title=title,
        authors=["Author A"],
        year=year,
        source=source,
        oa_status=oa_status,
        sources_found_in=[source],
    )


def _sample_records() -> list[UnifiedRecord]:
    return [
        _build_unified_record(
            record_id="r1",
            source=SourceType.PUBMED,
            year=2018,
            oa_status=OAStatus.OPEN,
            title="Record 1",
        ),
        _build_unified_record(
            record_id="r2",
            source=SourceType.OPENALEX,
            year=2020,
            oa_status=OAStatus.CLOSED,
            title="Record 2",
        ),
        _build_unified_record(
            record_id="r3",
            source=SourceType.EUROPEPMC,
            year=2021,
            oa_status=OAStatus.OPEN,
            title="Record 3",
        ),
        _build_unified_record(
            record_id="r4",
            source=SourceType.CLINICALTRIALS,
            year=None,
            oa_status=OAStatus.UNKNOWN,
            title="Record 4",
        ),
    ]


def test_basic_counts_calculation() -> None:
    service = PrismaService()
    records = _sample_records()

    counts = service.compute_counts(identified=10, records=records, filters=PrismaFilters())

    assert counts.identified == 10
    assert counts.after_deduplication == 4
    assert counts.screened == 4
    assert counts.excluded == 0
    assert counts.oa_retrieved == 2


def test_year_filter_narrows_results() -> None:
    service = PrismaService()
    records = _sample_records()
    filters = PrismaFilters(year_from=2020, year_to=2021)

    counts = service.compute_counts(identified=10, records=records, filters=filters)

    assert counts.after_deduplication == 4
    assert counts.screened == 4
    assert counts.excluded == 2
    assert counts.oa_retrieved == 1


def test_source_filter() -> None:
    service = PrismaService()
    records = _sample_records()
    filters = PrismaFilters(sources=[SourceType.PUBMED, SourceType.OPENALEX])

    counts = service.compute_counts(identified=10, records=records, filters=filters)

    assert counts.excluded == 2
    assert counts.oa_retrieved == 1


def test_oa_only_filter() -> None:
    service = PrismaService()
    records = _sample_records()
    filters = PrismaFilters(open_access_only=True)

    counts = service.compute_counts(identified=10, records=records, filters=filters)

    assert counts.excluded == 2
    assert counts.oa_retrieved == 2


def test_combined_filters() -> None:
    service = PrismaService()
    records = _sample_records()
    filters = PrismaFilters(
        year_from=2020,
        year_to=2021,
        sources=[SourceType.EUROPEPMC, SourceType.OPENALEX],
        open_access_only=True,
    )

    counts = service.compute_counts(identified=10, records=records, filters=filters)

    assert counts.excluded == 3
    assert counts.oa_retrieved == 1


def test_no_filters() -> None:
    service = PrismaService()
    records = _sample_records()

    counts = service.compute_counts(identified=10, records=records, filters=None)

    assert counts.after_deduplication == 4
    assert counts.screened == 4
    assert counts.excluded == 0
    assert counts.oa_retrieved == 2
