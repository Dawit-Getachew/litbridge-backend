"""Frozen snapshots of the public API surface used by the frontend.

These tests fail if any field is added, removed, renamed, or retyped in a
publicly returned schema or in the SSE event vocabulary. They protect the
frontend (which is not being updated alongside backend ranking changes) from
accidental contract drift caused by the free-text relevance overhaul.

If a change to one of these snapshots is intentional, update the snapshot in
the same commit and review the diff carefully — the frontend will need to be
updated to match.
"""

from __future__ import annotations

import types
import typing
from typing import Any, get_args, get_origin

import pytest

from src.schemas.enrichment import EnrichmentResponse
from src.schemas.enums import (
    AgeGroup,
    OAStatus,
    QueryType,
    SearchMode,
    SourceType,
    StudyType,
)
from src.schemas.records import PaginatedResults, RawRecord, UnifiedRecord
from src.schemas.search import (
    SearchHistoryItem,
    SearchHistoryResponse,
    SearchRequest,
    SearchResponse,
    SearchStatusResponse,
)
from src.schemas.streaming import StreamEvent, StreamEventType


def _field_summary(model: type) -> dict[str, str]:
    """Return a stable {field_name: type_name} mapping for one Pydantic model."""

    def _format(annotation: Any) -> str:
        origin = get_origin(annotation)
        if origin is None:
            if isinstance(annotation, type):
                return annotation.__name__
            return str(annotation)
        args = ", ".join(_format(arg) for arg in get_args(annotation))
        # Normalize both PEP 604 ('X | None') and typing.Union[...] to "Union[...]"
        # so the snapshot is stable across syntactic styles.
        if origin is typing.Union or origin is types.UnionType:
            return f"Union[{args}]"
        origin_name = getattr(origin, "__name__", None) or str(origin)
        return f"{origin_name}[{args}]"

    summary: dict[str, str] = {}
    for name, info in model.model_fields.items():
        summary[name] = _format(info.annotation)
    return summary


# -----------------------------------------------------------------------------
# Public response schemas (returned in API JSON bodies)
# -----------------------------------------------------------------------------


def test_unified_record_field_snapshot_locked() -> None:
    """The frontend reads UnifiedRecord on /api/v1/results/{id}; lock its shape."""
    expected = {
        "id": "str",
        "title": "str",
        "authors": "list[str]",
        "journal": "Union[str, NoneType]",
        "year": "Union[int, NoneType]",
        "doi": "Union[str, NoneType]",
        "pmid": "Union[str, NoneType]",
        "source": "SourceType",
        "sources_found_in": "list[SourceType]",
        "tldr": "Union[str, NoneType]",
        "citation_count": "Union[int, NoneType]",
        "oa_status": "OAStatus",
        "pdf_url": "Union[str, NoneType]",
        "abstract": "Union[str, NoneType]",
        "duplicate_cluster_id": "Union[str, NoneType]",
        "prisma_stage": "Union[str, NoneType]",
        "age_groups": "list[AgeGroup]",
        "age_min": "Union[int, NoneType]",
        "age_max": "Union[int, NoneType]",
        "study_type": "Union[StudyType, NoneType]",
    }
    assert _field_summary(UnifiedRecord) == expected


def test_paginated_results_field_snapshot_locked() -> None:
    expected = {
        "search_id": "str",
        "total_count": "int",
        "records": "list[UnifiedRecord]",
        "next_cursor": "Union[str, NoneType]",
    }
    assert _field_summary(PaginatedResults) == expected


def test_search_response_field_snapshot_locked() -> None:
    assert _field_summary(SearchResponse) == {"search_id": "str"}


def test_search_status_response_field_snapshot_locked() -> None:
    expected = {
        "search_id": "str",
        "status": "Literal[processing, completed, failed]",
        "total_count": "int",
        "sources_completed": "list[SourceType]",
        "sources_failed": "list[SourceType]",
        "progress_pct": "int",
    }
    assert _field_summary(SearchStatusResponse) == expected


def test_search_history_item_field_snapshot_locked() -> None:
    expected = {
        "id": "UUID",
        "query": "str",
        "query_type": "str",
        "search_mode": "str",
        "sources": "list[str]",
        "status": "str",
        "total_after_dedup": "int",
        "created_at": "datetime",
        "updated_at": "datetime",
    }
    assert _field_summary(SearchHistoryItem) == expected


def test_search_history_response_field_snapshot_locked() -> None:
    expected = {
        "searches": "list[SearchHistoryItem]",
        "total": "int",
        "next_cursor": "Union[str, NoneType]",
    }
    assert _field_summary(SearchHistoryResponse) == expected


def test_enrichment_response_field_snapshot_locked() -> None:
    expected = {
        "id": "str",
        "tldr": "Union[str, NoneType]",
        "citation_count": "Union[int, NoneType]",
        "oa_status": "Union[OAStatus, NoneType]",
        "pdf_url": "Union[str, NoneType]",
    }
    assert _field_summary(EnrichmentResponse) == expected


# -----------------------------------------------------------------------------
# Public request schema
# -----------------------------------------------------------------------------


def test_search_request_field_snapshot_locked() -> None:
    """The frontend posts SearchRequest; locking input keeps old clients valid."""
    expected = {
        "query": "str",
        "query_type": "QueryType",
        "search_mode": "SearchMode",
        "sources": "Union[list[SourceType], NoneType]",
        "pico": "Union[PICOInput, NoneType]",
        "max_results": "int",
        "workflow": "bool",
    }
    assert _field_summary(SearchRequest) == expected


# -----------------------------------------------------------------------------
# Internal schemas — ranking-only fields must never leak to the public surface
# -----------------------------------------------------------------------------


def test_unified_record_does_not_expose_internal_ranking_fields() -> None:
    """source_rank and any ranking metadata must remain internal-only."""
    forbidden = {
        "source_rank",
        "rrf_score",
        "fused_score",
        "title_boost",
        "recency_boost",
        "ranking_signals",
    }
    assert set(UnifiedRecord.model_fields).isdisjoint(forbidden), (
        "Public UnifiedRecord must not expose internal ranking signals; the "
        "frontend will break or treat them as unknown fields."
    )


def test_paginated_results_does_not_expose_internal_ranking_fields() -> None:
    forbidden = {"ranking_version", "fused_score", "rrf_k"}
    assert set(PaginatedResults.model_fields).isdisjoint(forbidden)


# -----------------------------------------------------------------------------
# Enum snapshots — values are wire-format and must not change
# -----------------------------------------------------------------------------


def test_source_type_enum_values_locked() -> None:
    assert {member.value for member in SourceType} == {
        "pubmed",
        "europepmc",
        "openalex",
        "clinicaltrials",
    }


def test_oa_status_enum_values_locked() -> None:
    assert {member.value for member in OAStatus} == {"open", "closed", "unknown"}


def test_query_type_enum_values_locked() -> None:
    assert {member.value for member in QueryType} == {
        "free",
        "structured",
        "boolean",
        "abstract",
    }


def test_search_mode_enum_values_locked() -> None:
    assert {member.value for member in SearchMode} == {
        "quick",
        "deep_research",
        "deep_analyze",
        "deep_thinking",
        "light_thinking",
    }


def test_age_group_enum_values_locked() -> None:
    assert {member.value for member in AgeGroup} == {"child", "adult", "older_adult"}


def test_study_type_enum_values_locked() -> None:
    assert {member.value for member in StudyType} == {
        "interventional",
        "observational",
        "expanded_access",
        "diagnostic",
        "other",
    }


# -----------------------------------------------------------------------------
# SSE event vocabulary — every value is a wire-format string the frontend
# selects on. Adding new event types is allowed (additive); removing or
# renaming any existing value would break the frontend.
# -----------------------------------------------------------------------------


_LOCKED_STREAM_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "search_started",
        "status",
        "source_searching",
        "source_completed",
        "source_failed",
        "dedup_completed",
        "enrichment_update",
        "record_enriched",
        "thinking",
        "search_completed",
        "chat_started",
        "citation",
        "chat_completed",
        "error",
    }
)


def test_stream_event_type_vocabulary_is_a_superset_of_locked_set() -> None:
    """Frontend may rely on any of these event names — none can disappear."""
    actual: set[str] = set(get_args(StreamEventType))
    missing = _LOCKED_STREAM_EVENT_TYPES - actual
    assert not missing, (
        f"StreamEventType is missing previously published events: {sorted(missing)}. "
        "Removing/renaming these would break the SSE consumer in the frontend."
    )


def test_stream_event_top_level_keys_locked() -> None:
    """Each SSE payload must always have exactly {'event', 'data'} at the top."""
    assert set(StreamEvent.model_fields.keys()) == {"event", "data"}
    assert StreamEvent.model_fields["event"].annotation is StreamEventType
    assert _field_summary(StreamEvent)["data"] == "dict[str, Any]"


# -----------------------------------------------------------------------------
# Internal-only schema (RawRecord) — frontend never sees this. We DO assert it
# stays *invisible* to the API by checking the public surface separately above.
# This snapshot lets us safely add ranking fields here without breaking clients.
# -----------------------------------------------------------------------------


def test_raw_record_minimum_field_set_present() -> None:
    """RawRecord may grow internal fields, but core ones must stay."""
    required = {
        "source_id",
        "source",
        "title",
        "authors",
        "journal",
        "year",
        "doi",
        "pmid",
        "abstract",
        "pdf_url",
        "oa_status",
        "raw_data",
    }
    assert required.issubset(set(RawRecord.model_fields.keys()))


# -----------------------------------------------------------------------------
# JSON serialization parity — make sure adding internal fields to RawRecord
# does NOT cause them to bleed into UnifiedRecord JSON output.
# -----------------------------------------------------------------------------


def test_unified_record_json_keys_are_a_subset_of_locked_field_names() -> None:
    """Serialized UnifiedRecord must only emit the locked field names."""
    sample = UnifiedRecord(
        id="rec-1",
        title="Sample",
        authors=["A. Author"],
        source=SourceType.PUBMED,
        sources_found_in=[SourceType.PUBMED],
    )
    payload = sample.model_dump(mode="json")
    locked = {
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
        "age_groups",
        "age_min",
        "age_max",
        "study_type",
    }
    assert set(payload.keys()) == locked


@pytest.mark.parametrize(
    "schema",
    [
        UnifiedRecord,
        PaginatedResults,
        SearchResponse,
        SearchStatusResponse,
        SearchHistoryItem,
        SearchHistoryResponse,
        EnrichmentResponse,
        SearchRequest,
        StreamEvent,
    ],
)
def test_public_schema_field_names_are_snake_case(schema: type) -> None:
    """All public schemas must expose snake_case field names."""
    import re

    snake_case = re.compile(r"^[a-z][a-z0-9_]*$")
    for field_name in schema.model_fields:
        assert snake_case.fullmatch(field_name), (
            f"Field '{field_name}' on {schema.__name__} is not snake_case; the frontend "
            "expects snake_case keys throughout."
        )
