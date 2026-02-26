"""Tests for deduplication service golden-record behavior."""

from __future__ import annotations

import time

from src.schemas.enums import OAStatus, SourceType
from src.schemas.records import RawRecord
from src.services.dedup_service import DedupService


def _build_raw_record(
    *,
    source_id: str,
    source: SourceType,
    title: str,
    authors: list[str] | None = None,
    journal: str | None = None,
    year: int | None = None,
    doi: str | None = None,
    pmid: str | None = None,
    abstract: str | None = None,
    pdf_url: str | None = None,
    oa_status: OAStatus = OAStatus.UNKNOWN,
) -> RawRecord:
    return RawRecord(
        source_id=source_id,
        source=source,
        title=title,
        authors=authors or [],
        journal=journal,
        year=year,
        doi=doi,
        pmid=pmid,
        abstract=abstract,
        pdf_url=pdf_url,
        oa_status=oa_status,
    )


def test_doi_hard_match() -> None:
    service = DedupService()
    records = [
        _build_raw_record(
            source_id="pm-1",
            source=SourceType.PUBMED,
            title="Metformin trial",
            year=2021,
            doi="10.1000/metformin.1",
        ),
        _build_raw_record(
            source_id="oa-1",
            source=SourceType.OPENALEX,
            title="Metformin trial from OpenAlex",
            year=2021,
            doi="https://doi.org/10.1000/METFORMIN.1",
        ),
        _build_raw_record(
            source_id="epmc-1",
            source=SourceType.EUROPEPMC,
            title="Metformin trial from EuropePMC",
            year=2021,
            doi="http://doi.org/10.1000/metformin.1",
        ),
    ]

    deduped = service.deduplicate(records)

    assert len(deduped) == 1
    assert deduped[0].doi == "10.1000/metformin.1"
    assert set(deduped[0].sources_found_in) == {
        SourceType.PUBMED,
        SourceType.OPENALEX,
        SourceType.EUROPEPMC,
    }


def test_pmid_hard_match() -> None:
    service = DedupService()
    records = [
        _build_raw_record(
            source_id="pm-11",
            source=SourceType.PUBMED,
            title="Aspirin outcomes",
            year=2019,
            pmid="123456",
        ),
        _build_raw_record(
            source_id="epmc-11",
            source=SourceType.EUROPEPMC,
            title="Aspirin outcomes in cardiovascular disease",
            year=2019,
            pmid="123456",
        ),
    ]

    deduped = service.deduplicate(records)

    assert len(deduped) == 1
    assert deduped[0].pmid == "123456"
    assert set(deduped[0].sources_found_in) == {SourceType.PUBMED, SourceType.EUROPEPMC}


def test_fuzzy_title_match() -> None:
    service = DedupService()
    records = [
        _build_raw_record(
            source_id="pm-fuzzy",
            source=SourceType.PUBMED,
            title="Metformin and cardiovascular outcomes",
            year=2020,
        ),
        _build_raw_record(
            source_id="oa-fuzzy",
            source=SourceType.OPENALEX,
            title="Metformin & Cardiovascular Outcomes",
            year=2020,
        ),
    ]

    deduped = service.deduplicate(records)

    assert len(deduped) == 1
    assert set(deduped[0].sources_found_in) == {SourceType.PUBMED, SourceType.OPENALEX}


def test_no_match() -> None:
    service = DedupService()
    records = [
        _build_raw_record(
            source_id="n1",
            source=SourceType.PUBMED,
            title="Effects of magnesium on sleep",
            year=2018,
            doi="10.1000/sleep.1",
        ),
        _build_raw_record(
            source_id="n2",
            source=SourceType.OPENALEX,
            title="Cancer immunotherapy landscape review",
            year=2022,
            doi="10.1000/oncology.22",
        ),
    ]

    deduped = service.deduplicate(records)

    assert len(deduped) == 2


def test_field_merging() -> None:
    service = DedupService()
    records = [
        _build_raw_record(
            source_id="merge-a",
            source=SourceType.PUBMED,
            title="Blood pressure outcomes in diabetes",
            year=2021,
            abstract="Detailed abstract text from source A.",
            authors=["Jane Doe"],
        ),
        _build_raw_record(
            source_id="merge-b",
            source=SourceType.OPENALEX,
            title="Blood pressure outcomes in diabetes",
            year=2021,
            doi="10.1000/bp.2021",
        ),
    ]

    deduped = service.deduplicate(records)

    assert len(deduped) == 1
    assert deduped[0].abstract == "Detailed abstract text from source A."
    assert deduped[0].doi == "10.1000/bp.2021"


def test_performance() -> None:
    service = DedupService()

    unique_count = 3500
    duplicate_count = 1500  # ~30% of the 5000 total records are duplicates.
    records: list[RawRecord] = []

    for index in range(unique_count):
        records.append(
            _build_raw_record(
                source_id=f"u-{index}",
                source=SourceType.PUBMED,
                title=f"Unique trial title {index}",
                year=2000 + (index % 20),
                doi=f"10.9999/perf.{index}",
                authors=["A. Researcher"],
            )
        )

    for index in range(duplicate_count):
        records.append(
            _build_raw_record(
                source_id=f"d-{index}",
                source=SourceType.OPENALEX,
                title=f"Duplicate trial title {index}",
                year=2000 + (index % 20),
                doi=f"https://doi.org/10.9999/PERF.{index}",
                authors=["B. Scientist"],
            )
        )

    started_at = time.perf_counter()
    deduped = service.deduplicate(records)
    elapsed_seconds = time.perf_counter() - started_at

    assert len(deduped) == unique_count
    assert elapsed_seconds < 2.0


def test_doi_normalization() -> None:
    service = DedupService()
    records = [
        _build_raw_record(
            source_id="norm-1",
            source=SourceType.PUBMED,
            title="Normalization title",
            year=2023,
            doi="https://doi.org/10.5555/ABC.DEF",
        ),
        _build_raw_record(
            source_id="norm-2",
            source=SourceType.EUROPEPMC,
            title="Normalization title variation",
            year=2023,
            doi="10.5555/abc.def",
        ),
    ]

    deduped = service.deduplicate(records)

    assert len(deduped) == 1
    assert deduped[0].doi == "10.5555/abc.def"


def test_empty_input() -> None:
    service = DedupService()
    assert service.deduplicate([]) == []
