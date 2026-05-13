"""Tests for PRISMA count computations."""

from __future__ import annotations

from src.schemas.enums import AgeGroup, OAStatus, SourceType, StudyDesign
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
    age_groups: list[AgeGroup] | None = None,
    age_min: int | None = None,
    age_max: int | None = None,
    study_design: StudyDesign | None = None,
) -> UnifiedRecord:
    return UnifiedRecord(
        id=record_id,
        title=title,
        authors=["Author A"],
        year=year,
        source=source,
        oa_status=oa_status,
        sources_found_in=[source],
        age_groups=age_groups or [],
        age_min=age_min,
        age_max=age_max,
        study_design=study_design,
    )


def _sample_records() -> list[UnifiedRecord]:
    return [
        _build_unified_record(
            record_id="r1",
            source=SourceType.PUBMED,
            year=2018,
            oa_status=OAStatus.OPEN,
            title="Record 1",
            age_groups=[AgeGroup.ADULT],
            age_min=18,
            age_max=65,
            study_design=StudyDesign.RCT,
        ),
        _build_unified_record(
            record_id="r2",
            source=SourceType.OPENALEX,
            year=2020,
            oa_status=OAStatus.CLOSED,
            title="Record 2",
            age_groups=[AgeGroup.CHILD],
            age_min=0,
            age_max=17,
            study_design=StudyDesign.OBSERVATIONAL,
        ),
        _build_unified_record(
            record_id="r3",
            source=SourceType.EUROPEPMC,
            year=2021,
            oa_status=OAStatus.OPEN,
            title="Record 3",
            age_groups=[AgeGroup.ADULT, AgeGroup.OLDER_ADULT],
            age_min=18,
            age_max=90,
            study_design=StudyDesign.RCT,
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


def test_age_group_filter_single() -> None:
    service = PrismaService()
    records = _sample_records()
    filters = PrismaFilters(age_groups=[AgeGroup.CHILD])

    counts = service.compute_counts(identified=10, records=records, filters=filters)

    assert counts.excluded == 3
    assert counts.oa_retrieved == 0


def test_age_group_filter_multiple() -> None:
    service = PrismaService()
    records = _sample_records()
    filters = PrismaFilters(age_groups=[AgeGroup.ADULT, AgeGroup.OLDER_ADULT])

    counts = service.compute_counts(identified=10, records=records, filters=filters)

    assert counts.excluded == 2
    assert counts.oa_retrieved == 2


def test_age_min_filter() -> None:
    service = PrismaService()
    records = _sample_records()
    filters = PrismaFilters(age_min=18)

    counts = service.compute_counts(identified=10, records=records, filters=filters)

    assert counts.excluded == 1
    assert counts.oa_retrieved == 2


def test_age_max_filter() -> None:
    service = PrismaService()
    records = _sample_records()
    filters = PrismaFilters(age_max=17)

    counts = service.compute_counts(identified=10, records=records, filters=filters)

    assert counts.excluded == 2
    assert counts.oa_retrieved == 0


def test_age_range_overlap_filter() -> None:
    service = PrismaService()
    records = _sample_records()
    filters = PrismaFilters(age_min=10, age_max=20)

    counts = service.compute_counts(identified=10, records=records, filters=filters)

    assert counts.excluded == 0
    assert counts.oa_retrieved == 2


def test_study_design_filter_single() -> None:
    service = PrismaService()
    records = _sample_records()
    filters = PrismaFilters(study_designs=[StudyDesign.RCT])

    counts = service.compute_counts(identified=10, records=records, filters=filters)

    assert counts.excluded == 2
    assert counts.oa_retrieved == 2


def test_study_design_filter_multiple() -> None:
    service = PrismaService()
    records = _sample_records()
    filters = PrismaFilters(study_designs=[StudyDesign.RCT, StudyDesign.OBSERVATIONAL])

    counts = service.compute_counts(identified=10, records=records, filters=filters)

    assert counts.excluded == 1
    assert counts.oa_retrieved == 2


def test_study_design_excludes_records_with_no_design() -> None:
    """Per Phase 1.5: unclassifiable records (None) are excluded from the filter."""
    service = PrismaService()
    records = _sample_records()
    # r4 has study_design=None; filtering for any specific design should drop it.
    filters = PrismaFilters(study_designs=[StudyDesign.SYSTEMATIC_REVIEW])

    counts = service.compute_counts(identified=10, records=records, filters=filters)

    assert counts.excluded == 4
    assert counts.oa_retrieved == 0


def test_combined_age_and_study_design_filters() -> None:
    service = PrismaService()
    records = _sample_records()
    filters = PrismaFilters(
        age_groups=[AgeGroup.ADULT],
        study_designs=[StudyDesign.RCT],
    )

    counts = service.compute_counts(identified=10, records=records, filters=filters)

    assert counts.excluded == 2
    assert counts.oa_retrieved == 2
