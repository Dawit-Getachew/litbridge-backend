"""Unit tests for Free-Text Search v3 Overhaul ranking behavior.

These tests pin the *v3* ranking contract: abstract-match boost,
citation-count boost, per-source weight changes (PubMed 2.5x, OpenAlex
0.5x, CT.gov 0 for FREE), the BM25 reranker blend, PubMed-primary
ordering for FREE+Quick, and the MedCPT flag gating.

Each test stays narrowly scoped so a regression in any single signal
shows up as a focused failure, not a whole-pipeline surprise.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.core.config import Settings
from src.ranking.bm25_reranker import BM25Reranker
from src.schemas.enums import OAStatus, QueryType, SearchMode, SourceType
from src.schemas.records import RawRecord
from src.services.dedup_service import DedupService


def _record(
    *,
    source_id: str,
    source: SourceType,
    title: str,
    year: int | None = 2022,
    doi: str | None = None,
    source_rank: int = 0,
    abstract: str | None = None,
    citation_count: int | None = None,
) -> RawRecord:
    return RawRecord(
        source_id=source_id,
        source=source,
        title=title,
        authors=[],
        journal=None,
        year=year,
        doi=doi,
        pmid=None,
        abstract=abstract,
        pdf_url=None,
        oa_status=OAStatus.UNKNOWN,
        source_rank=source_rank,
        citation_count=citation_count,
    )


# -----------------------------------------------------------------------------
# Abstract-match boost (v3 Phase A)
# -----------------------------------------------------------------------------


def test_abstract_match_boost_lifts_paper_with_query_terms_in_abstract() -> None:
    """At equal RRF + equal title-match, the paper whose abstract contains
    every query term should outrank one with an empty abstract.

    BM25 is disabled here so we isolate the abstract-match boost —
    otherwise the rich-abstract paper would win either way and the test
    wouldn't pin the specific signal."""
    settings = Settings(RANKING_BM25_WEIGHT=0.0)
    service = DedupService(settings=settings)
    records = [
        _record(
            source_id="pm-barren",
            source=SourceType.PUBMED,
            title="Metformin research alpha topic",
            source_rank=5,
            abstract=None,
            doi="10.1/barren",
        ),
        _record(
            source_id="pm-rich",
            source=SourceType.PUBMED,
            title="Sitagliptin trial beta subject",
            source_rank=5,
            abstract="We studied glp-1 antagonists and their effect on cholesterol",
            doi="10.1/rich",
        ),
    ]
    deduped = service.deduplicate(
        records, query="glp-1 antagonists cholesterol", query_type=QueryType.FREE,
    )
    assert deduped[0].doi == "10.1/rich"


def test_abstract_boost_disabled_when_setting_zero() -> None:
    """With RANKING_ABSTRACT_BOOST=0.0 the abstract signal is a no-op, so
    only the tiebreakers (source-enum, source-id) decide order between two
    equal-RRF non-duplicate clusters — i.e. the paper with the rich
    abstract does NOT get lifted ahead of the bare one."""
    settings = Settings(RANKING_ABSTRACT_BOOST=0.0, RANKING_BM25_WEIGHT=0.0)
    service = DedupService(settings=settings)
    records = [
        _record(
            source_id="pm-a",
            source=SourceType.PUBMED,
            title="Metformin research in adults alpha",
            source_rank=5,
            abstract=None,
            doi="10.1/a",
        ),
        _record(
            source_id="pm-b",
            source=SourceType.PUBMED,
            title="Sitagliptin trial beta different topic",
            source_rank=5,
            abstract="glp-1 antagonists cholesterol",
            doi="10.1/b",
        ),
    ]
    deduped = service.deduplicate(
        records, query="glp-1 antagonists cholesterol", query_type=QueryType.FREE,
    )
    # With abstract-boost off both clusters are equal on RRF so the
    # source_id lexicographic tiebreaker picks ``10.1/a`` first.
    assert deduped[0].doi == "10.1/a"


# -----------------------------------------------------------------------------
# Citation-count boost (v3 Phase A)
# -----------------------------------------------------------------------------


def test_citation_boost_lifts_highly_cited_paper_at_equal_rank() -> None:
    """Two otherwise-equivalent clusters: the one with 5,000 citations wins.

    BM25 is disabled to isolate the citation-count boost — both titles
    match the query equally well, so any reordering is attributable to
    the citation signal."""
    settings = Settings(RANKING_BM25_WEIGHT=0.0)
    service = DedupService(settings=settings)
    records = [
        _record(
            source_id="pm-low",
            source=SourceType.PUBMED,
            title="Landmark study topic one distinct",
            source_rank=10,
            doi="10.1/low",
            citation_count=5,
        ),
        _record(
            source_id="pm-high",
            source=SourceType.PUBMED,
            title="Landmark study topic two distinct",
            source_rank=10,
            doi="10.1/high",
            citation_count=5_000,
        ),
    ]
    deduped = service.deduplicate(
        records, query="landmark topic", query_type=QueryType.FREE,
    )
    assert deduped[0].doi == "10.1/high"


def test_citation_boost_is_capped_so_a_single_seminal_paper_cannot_swamp() -> None:
    """A fresh rank-1 paper should beat a 1M-cite rank-30 seminal one.
    With the citation cap at 0.4 a rank-30 paper's RRF contribution is
    too small to overcome rank-1's 30x RRF advantage, even at 1M cites."""
    settings = Settings(RANKING_BM25_WEIGHT=0.0)
    service = DedupService(settings=settings)
    records = [
        _record(
            source_id="pm-fresh",
            source=SourceType.PUBMED,
            title="Fresh rank 1 result alpha",
            source_rank=1,
            doi="10.1/fresh",
            citation_count=10,
        ),
        _record(
            source_id="pm-seminal",
            source=SourceType.PUBMED,
            title="Seminal old work beta distinct",
            source_rank=30,
            doi="10.1/seminal",
            citation_count=1_000_000,
        ),
    ]
    deduped = service.deduplicate(
        records, query="rank alpha beta", query_type=QueryType.FREE,
    )
    assert deduped[0].doi == "10.1/fresh"


# -----------------------------------------------------------------------------
# Per-source weights: OpenAlex down-weighted, CT.gov zeroed for FREE
# -----------------------------------------------------------------------------


def test_openalex_weight_default_half_pubmed_at_equal_rank() -> None:
    """At rank=1 across both sources, the 0.5x OpenAlex weight should put
    the PubMed paper ahead despite equal rank."""
    service = DedupService()
    records = [
        _record(
            source_id="oa-1",
            source=SourceType.OPENALEX,
            title="Topic result from OA",
            source_rank=1,
            doi="10.1/oa",
        ),
        _record(
            source_id="pm-1",
            source=SourceType.PUBMED,
            title="Topic result from PubMed",
            source_rank=1,
            doi="10.1/pm",
        ),
    ]
    deduped = service.deduplicate(
        records, query="topic result", query_type=QueryType.FREE,
    )
    assert deduped[0].source is SourceType.PUBMED


def test_ctgov_zero_weight_for_free_query_pushes_trial_to_end() -> None:
    """CT.gov entries should not outrank an equally-ranked PubMed article
    on a FREE query — weight 0 => zero RRF contribution."""
    service = DedupService()
    records = [
        _record(
            source_id="ct-1",
            source=SourceType.CLINICALTRIALS,
            title="NCT trial matching topic",
            source_rank=1,
            doi="10.1/ct",
        ),
        _record(
            source_id="pm-1",
            source=SourceType.PUBMED,
            title="PubMed topic article",
            source_rank=30,
            doi="10.1/pm",
        ),
    ]
    deduped = service.deduplicate(
        records, query="topic article", query_type=QueryType.FREE,
    )
    assert deduped[0].source is SourceType.PUBMED
    assert deduped[-1].source is SourceType.CLINICALTRIALS


def test_ctgov_participates_when_query_type_is_not_free() -> None:
    """For BOOLEAN/PICO queries the CT.gov weight defaults back to 1.0 so
    PICO searches that explicitly include trial protocols still surface."""
    service = DedupService()
    # Use a PICO / structured type to exercise the non-FREE path.
    records = [
        _record(
            source_id="ct-only",
            source=SourceType.CLINICALTRIALS,
            title="NCT trial only result",
            source_rank=1,
            doi="10.1/ct",
        ),
    ]
    deduped = service.deduplicate(
        records, query="trial", query_type=QueryType.PICO,
    )
    assert len(deduped) == 1
    assert deduped[0].source is SourceType.CLINICALTRIALS


# -----------------------------------------------------------------------------
# BM25 reranker (Phase B)
# -----------------------------------------------------------------------------


def test_bm25_reranker_disabled_when_weight_zero_preserves_rrf_order() -> None:
    """Weight=0 is the escape hatch; order must match the pre-BM25 path."""
    settings = Settings(RANKING_BM25_WEIGHT=0.0)
    service = DedupService(settings=settings)
    records = [
        _record(
            source_id="pm-a",
            source=SourceType.PUBMED,
            title="Topic a",
            source_rank=1,
            doi="10.1/a",
        ),
        _record(
            source_id="pm-b",
            source=SourceType.PUBMED,
            title="Topic b",
            source_rank=2,
            doi="10.1/b",
        ),
    ]
    deduped = service.deduplicate(records, query="topic", query_type=QueryType.FREE)
    assert [r.doi for r in deduped] == ["10.1/a", "10.1/b"]


def test_bm25_reranker_is_resilient_to_empty_query() -> None:
    """BM25Reranker.score should return all-zeros for an empty query."""
    reranker = BM25Reranker()
    records = [
        _record(
            source_id="x",
            source=SourceType.PUBMED,
            title="Some title",
            abstract="Some abstract text about metformin",
            doi="10.1/x",
        ),
    ]
    assert reranker.score(query="", records=records) == [0.0]
    assert reranker.score(query="   ", records=records) == [0.0]


def test_bm25_reranker_scores_are_non_negative_and_length_matches() -> None:
    reranker = BM25Reranker()
    records = [
        _record(
            source_id="x",
            source=SourceType.PUBMED,
            title="Metformin for type 2 diabetes",
            abstract="Metformin lowered HbA1c in adults with type 2 diabetes mellitus.",
            doi="10.1/x",
        ),
        _record(
            source_id="y",
            source=SourceType.PUBMED,
            title="Aspirin cardiovascular prevention",
            abstract="Aspirin prevented MACE in high-risk adults.",
            doi="10.1/y",
        ),
    ]
    scores = reranker.score(query="metformin diabetes", records=records)
    assert len(scores) == 2
    assert all(score >= 0.0 for score in scores)
    assert scores[0] >= scores[1]


# -----------------------------------------------------------------------------
# PubMed-primary ordering (FREE + QUICK only)
# -----------------------------------------------------------------------------


def test_pubmed_primary_pins_pubmed_results_for_free_quick() -> None:
    """FREE + QUICK → top-15 PubMed records pinned in their source order."""
    service = DedupService()
    records = [
        _record(
            source_id="oa-rank1",
            source=SourceType.OPENALEX,
            title="OpenAlex rank 1 noise",
            source_rank=1,
            doi="10.1/oa1",
        ),
        _record(
            source_id="pm-rank-5",
            source=SourceType.PUBMED,
            title="PubMed rank 5",
            source_rank=5,
            doi="10.1/pm5",
        ),
        _record(
            source_id="pm-rank-2",
            source=SourceType.PUBMED,
            title="PubMed rank 2",
            source_rank=2,
            doi="10.1/pm2",
        ),
        _record(
            source_id="pm-rank-1",
            source=SourceType.PUBMED,
            title="PubMed rank 1",
            source_rank=1,
            doi="10.1/pm1",
        ),
    ]
    deduped = service.deduplicate(
        records,
        query="topic query",
        query_type=QueryType.FREE,
        search_mode=SearchMode.QUICK,
    )
    top_three = [record.doi for record in deduped[:3]]
    assert top_three == ["10.1/pm1", "10.1/pm2", "10.1/pm5"]


def test_pubmed_primary_skipped_for_free_deep_mode() -> None:
    """FREE + DEEP_RESEARCH → regular fused ordering; PubMed-primary off."""
    service = DedupService()
    records = [
        _record(
            source_id="pm-rank-5",
            source=SourceType.PUBMED,
            title="PubMed rank 5 topic",
            source_rank=5,
            doi="10.1/pm5",
        ),
        _record(
            source_id="oa-rank-1",
            source=SourceType.OPENALEX,
            title="OpenAlex rank 1 topic",
            source_rank=1,
            doi="10.1/oa1",
        ),
    ]
    deduped = service.deduplicate(
        records,
        query="topic",
        query_type=QueryType.FREE,
        search_mode=SearchMode.DEEP_RESEARCH,
    )
    # With PubMed 2.5x weight + rank 5 vs OpenAlex 0.5x + rank 1, PubMed
    # still leads on fused score; but ordering is chosen by fused logic,
    # not PubMed-primary pin.
    assert deduped[0].source is SourceType.PUBMED


def test_pubmed_primary_skipped_for_boolean() -> None:
    """BOOLEAN → RRF skipped entirely → PubMed-primary also off."""
    service = DedupService()
    records = [
        _record(
            source_id="pm-a",
            source=SourceType.PUBMED,
            title="A",
            source_rank=10,
            year=2010,
            doi="10.1/a",
        ),
        _record(
            source_id="pm-b",
            source=SourceType.PUBMED,
            title="B",
            source_rank=1,
            year=2024,
            doi="10.1/b",
        ),
    ]
    deduped = service.deduplicate(
        records,
        query="(a) AND (b)",
        query_type=QueryType.BOOLEAN,
        search_mode=SearchMode.QUICK,
    )
    # First-seen order preserved.
    assert [record.doi for record in deduped] == ["10.1/a", "10.1/b"]


# -----------------------------------------------------------------------------
# MedCPT flag gating (Phase D)
# -----------------------------------------------------------------------------


def test_medcpt_flag_off_by_default_no_rerank_attempted() -> None:
    """With the flag off, DedupService must not touch the MedCPT reranker
    even if a buggy stub would otherwise throw. We assert it by injecting
    a reranker whose ``rerank`` raises — disabled flag == never called."""
    service = DedupService()
    stub = MagicMock()
    stub.rerank.side_effect = AssertionError("rerank must not be called when flag is OFF")
    service._medcpt_reranker = stub

    records = [
        _record(
            source_id="pm-1",
            source=SourceType.PUBMED,
            title="A",
            source_rank=1,
            doi="10.1/a",
        ),
        _record(
            source_id="pm-2",
            source=SourceType.PUBMED,
            title="B",
            source_rank=2,
            doi="10.1/b",
        ),
    ]
    service.deduplicate(records, query="topic", query_type=QueryType.FREE)
    stub.rerank.assert_not_called()


def test_medcpt_flag_on_calls_reranker_and_uses_returned_order() -> None:
    """Flag on → stub reranker is called once and its reordering sticks."""
    settings = Settings(RANKING_MEDCPT=True, RANKING_MEDCPT_TOP_K=5)
    service = DedupService(settings=settings)

    records = [
        _record(
            source_id="pm-a",
            source=SourceType.PUBMED,
            title="A",
            source_rank=1,
            doi="10.1/a",
        ),
        _record(
            source_id="pm-b",
            source=SourceType.PUBMED,
            title="B",
            source_rank=2,
            doi="10.1/b",
        ),
    ]

    def fake_rerank(*, query: str, records: list[RawRecord]) -> list[RawRecord]:
        # Reverse the input order so we can observe rerank effect.
        return list(reversed(records))

    stub = MagicMock()
    stub.rerank.side_effect = fake_rerank
    service._medcpt_reranker = stub

    deduped = service.deduplicate(
        records,
        query="topic",
        query_type=QueryType.FREE,
        search_mode=SearchMode.DEEP_RESEARCH,  # skip PubMed-primary overlay
    )

    stub.rerank.assert_called_once()
    # After reverse of top-2, the originally second-ranked cluster wins.
    assert deduped[0].doi == "10.1/b"


def test_medcpt_exception_is_swallowed_and_returns_original_order() -> None:
    settings = Settings(RANKING_MEDCPT=True)
    service = DedupService(settings=settings)

    stub = MagicMock()
    stub.rerank.side_effect = RuntimeError("simulated backend outage")
    service._medcpt_reranker = stub

    records = [
        _record(
            source_id="pm-a",
            source=SourceType.PUBMED,
            title="Topic a",
            source_rank=1,
            doi="10.1/a",
        ),
        _record(
            source_id="pm-b",
            source=SourceType.PUBMED,
            title="Topic b",
            source_rank=2,
            doi="10.1/b",
        ),
    ]
    deduped = service.deduplicate(
        records,
        query="topic",
        query_type=QueryType.FREE,
        search_mode=SearchMode.DEEP_RESEARCH,
    )
    # MedCPT failure must fall back to the previous ordering (rank-1 first).
    assert [record.doi for record in deduped] == ["10.1/a", "10.1/b"]
