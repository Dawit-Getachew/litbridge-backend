"""Schema contract tests for LitBridge API boundaries."""

import pytest
from pydantic import ValidationError

from src.schemas import (
    OAStatus,
    PICOInput,
    PaginatedResults,
    QueryType,
    SearchMode,
    SearchRequest,
    SourceType,
    StreamEvent,
    UnifiedRecord,
)


def test_search_request_defaults_are_applied() -> None:
    """SearchRequest should apply expected API defaults."""

    request = SearchRequest(query="oncology")

    assert request.query_type is QueryType.FREE
    assert request.search_mode is SearchMode.QUICK
    assert request.max_results == 100
    assert request.sources == list(SourceType)


def test_search_request_pico_requires_pico_payload() -> None:
    """Structured query_type should require a pico object."""

    with pytest.raises(ValidationError):
        SearchRequest(query="heart failure", query_type=QueryType.PICO)

    request = SearchRequest(
        query="heart failure",
        query_type=QueryType.PICO,
        pico=PICOInput(population="adults"),
    )
    assert request.pico is not None


def test_search_request_rejects_empty_sources_list() -> None:
    """An empty source list is invalid."""

    with pytest.raises(ValidationError):
        SearchRequest(query="diabetes", sources=[])


def test_search_request_none_sources_defaults_to_all_sources() -> None:
    """None sources should normalize to all providers."""

    request = SearchRequest(query="immunotherapy", sources=None)
    assert request.sources == list(SourceType)


def test_enums_serialize_to_expected_strings() -> None:
    """Enums should serialize to stable contract strings."""

    request = SearchRequest(
        query="asthma",
        query_type=QueryType.BOOLEAN,
        search_mode=SearchMode.DEEP_RESEARCH,
        sources=[SourceType.PUBMED, SourceType.EUROPEPMC],
    )

    payload = request.model_dump(mode="json")

    assert QueryType.PICO.value == "structured"
    assert SearchMode.DEEP_ANALYZE.value == "deep_analyze"
    assert SourceType.CLINICALTRIALS.value == "clinicaltrials"
    assert OAStatus.UNKNOWN.value == "unknown"
    assert payload["query_type"] == "boolean"
    assert payload["search_mode"] == "deep_research"
    assert payload["sources"] == ["pubmed", "europepmc"]


def test_unified_record_json_matches_snake_case_contract() -> None:
    """UnifiedRecord JSON payload should match frontend API contract."""

    record = UnifiedRecord(
        id="rec-1",
        title="Cancer Immunotherapy Trial",
        authors=["Doe J", "Smith A"],
        source=SourceType.PUBMED,
        sources_found_in=[SourceType.PUBMED, SourceType.OPENALEX],
        year=2024,
        oa_status=OAStatus.OPEN,
        citation_count=42,
    )

    payload = record.model_dump(mode="json")
    expected_keys = {
        "id",
        "title",
        "authors",
        "journal",
        "year",
        "doi",
        "pmid",
        "source",
        "sources_found_in",
        "tldr",
        "citation_count",
        "oa_status",
        "pdf_url",
        "abstract",
        "duplicate_cluster_id",
        "prisma_stage",
    }

    assert set(payload.keys()) == expected_keys
    assert payload["source"] == "pubmed"
    assert payload["sources_found_in"] == ["pubmed", "openalex"]
    assert isinstance(payload["authors"], list)
    assert isinstance(payload["year"], int)
    assert isinstance(payload["citation_count"], int)


@pytest.mark.parametrize(
    "event_type",
    [
        "search_started",
        "source_completed",
        "source_failed",
        "dedup_completed",
        "enrichment_update",
        "thinking",
        "search_completed",
        "error",
    ],
)
def test_stream_event_accepts_all_supported_event_types(event_type: str) -> None:
    """StreamEvent should validate each allowed event literal."""

    event = StreamEvent(event=event_type, data={"ok": True})
    payload = event.model_dump(mode="json")

    assert payload["event"] == event_type
    assert payload["data"] == {"ok": True}


def test_paginated_results_handles_empty_records() -> None:
    """PaginatedResults should support an empty page."""

    payload = PaginatedResults(search_id="s1", total_count=0, records=[]).model_dump(mode="json")

    assert payload["search_id"] == "s1"
    assert payload["total_count"] == 0
    assert payload["records"] == []
    assert payload["next_cursor"] is None


def test_paginated_results_handles_populated_records() -> None:
    """PaginatedResults should serialize nested records correctly."""

    record = UnifiedRecord(
        id="rec-2",
        title="Hypertension Study",
        authors=["Lee K"],
        source=SourceType.CLINICALTRIALS,
    )
    page = PaginatedResults(
        search_id="s2",
        total_count=1,
        records=[record],
        next_cursor="cursor-123",
    )
    payload = page.model_dump(mode="json")

    assert payload["search_id"] == "s2"
    assert payload["total_count"] == 1
    assert len(payload["records"]) == 1
    assert payload["records"][0]["source"] == "clinicaltrials"
    assert payload["next_cursor"] == "cursor-123"
