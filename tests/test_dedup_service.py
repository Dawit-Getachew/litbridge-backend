"""Tests for deduplication service golden-record behavior."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from src.schemas.enums import OAStatus, QueryType, SourceType
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
    source_rank: int = 0,
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
        source_rank=source_rank,
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


# ---------------------------------------------------------------------------
# Phase 2: weighted Reciprocal Rank Fusion + boosts
# ---------------------------------------------------------------------------


def test_rrf_no_query_preserves_first_seen_order_backward_compat() -> None:
    """When query is None, the dedup output must match the pre-Phase-2
    first-seen cluster ordering — this is the contract every legacy test
    (and every legacy caller) relies on."""
    service = DedupService()
    records = [
        _build_raw_record(
            source_id="later-1",
            source=SourceType.PUBMED,
            title="Paper that appears later in input",
            year=2010,
            source_rank=99,
        ),
        _build_raw_record(
            source_id="first-1",
            source=SourceType.PUBMED,
            title="Paper that appears first in input",
            year=2020,
            source_rank=1,
        ),
    ]

    deduped = service.deduplicate(records)

    assert [record.title for record in deduped] == [
        "Paper that appears later in input",
        "Paper that appears first in input",
    ]


def test_rrf_boolean_query_type_skips_rrf_for_prisma_reproducibility() -> None:
    """BOOLEAN/PRISMA searches must not be re-ordered by RRF — reviewers
    expect the source's own (date-sorted) ordering to be preserved."""
    service = DedupService()
    records = [
        _build_raw_record(
            source_id="second",
            source=SourceType.PUBMED,
            title="Second paper by date",
            year=2010,
            source_rank=99,
        ),
        _build_raw_record(
            source_id="first",
            source=SourceType.PUBMED,
            title="First paper by date",
            year=2024,
            source_rank=1,
        ),
    ]

    deduped = service.deduplicate(
        records,
        query="(metformin) AND (cardiovascular)",
        query_type=QueryType.BOOLEAN,
    )

    assert [record.title for record in deduped] == [
        "Second paper by date",
        "First paper by date",
    ]


def test_rrf_free_text_promotes_higher_ranked_source() -> None:
    """With a FREE query and per-source ranks populated, a top-ranked
    PubMed paper should outrank a lower-ranked OpenAlex paper even when
    they're unrelated clusters of the same size."""
    service = DedupService()
    current_year = datetime.now(UTC).year
    records = [
        _build_raw_record(
            source_id="low-rank-oa",
            source=SourceType.OPENALEX,
            title="Unrelated but retrieved first by query order",
            year=current_year,
            doi="10.1/oa.low",
            source_rank=50,
        ),
        _build_raw_record(
            source_id="top-rank-pm",
            source=SourceType.PUBMED,
            title="Top PubMed Best Match result on GLP-1 and cholesterol",
            year=current_year,
            doi="10.1/pm.top",
            source_rank=1,
        ),
    ]

    deduped = service.deduplicate(
        records,
        query="GLP-1 antagonists cholesterol",
        query_type=QueryType.FREE,
    )

    assert deduped[0].source is SourceType.PUBMED


def test_rrf_three_source_cluster_outranks_single_source_cluster() -> None:
    """Multi-source clusters accumulate RRF contributions and should
    therefore outrank a single-source cluster at the same per-source rank."""
    service = DedupService()
    records = [
        _build_raw_record(
            source_id="pm-multi",
            source=SourceType.PUBMED,
            title="Consensus paper seen in three sources",
            year=2022,
            doi="10.1/consensus",
            source_rank=5,
        ),
        _build_raw_record(
            source_id="oa-multi",
            source=SourceType.OPENALEX,
            title="Consensus paper seen in three sources",
            year=2022,
            doi="10.1/consensus",
            source_rank=5,
        ),
        _build_raw_record(
            source_id="epmc-multi",
            source=SourceType.EUROPEPMC,
            title="Consensus paper seen in three sources",
            year=2022,
            doi="10.1/consensus",
            source_rank=5,
        ),
        _build_raw_record(
            source_id="pm-solo",
            source=SourceType.PUBMED,
            title="Solo paper only one source retrieved",
            year=2022,
            doi="10.1/solo",
            source_rank=1,
        ),
    ]

    deduped = service.deduplicate(
        records,
        query="consensus paper",
        query_type=QueryType.FREE,
    )

    assert [record.title for record in deduped] == [
        "Consensus paper seen in three sources",
        "Solo paper only one source retrieved",
    ]


def test_rrf_pubmed_weight_bias_at_equal_rank() -> None:
    """With default PubMed weight 1.3, a PubMed paper at rank N outranks
    an OpenAlex paper at the same rank N when they're unrelated clusters."""
    service = DedupService()
    records = [
        _build_raw_record(
            source_id="oa-eq",
            source=SourceType.OPENALEX,
            title="Topic alpha paper",
            year=2022,
            doi="10.1/alpha",
            source_rank=3,
        ),
        _build_raw_record(
            source_id="pm-eq",
            source=SourceType.PUBMED,
            title="Topic beta paper",
            year=2022,
            doi="10.1/beta",
            source_rank=3,
        ),
    ]

    deduped = service.deduplicate(
        records,
        query="topic",
        query_type=QueryType.FREE,
    )

    assert deduped[0].source is SourceType.PUBMED


def test_rrf_title_match_boost_lifts_paper_with_all_query_terms_in_title() -> None:
    """Papers whose title contains every meaningful query term should be
    boosted above papers with only partial title overlap, at the same RRF."""
    service = DedupService()
    records = [
        _build_raw_record(
            source_id="partial",
            source=SourceType.PUBMED,
            title="Statin therapy in elderly populations",
            year=2022,
            doi="10.1/partial",
            source_rank=1,
        ),
        _build_raw_record(
            source_id="full",
            source=SourceType.PUBMED,
            title="GLP-1 antagonists effect on high cholesterol",
            year=2022,
            doi="10.1/full",
            source_rank=1,
        ),
    ]

    deduped = service.deduplicate(
        records,
        query="GLP-1 antagonists on high cholesterol",
        query_type=QueryType.FREE,
    )

    assert deduped[0].title == "GLP-1 antagonists effect on high cholesterol"


def test_rrf_recency_boost_lifts_current_year_over_five_year_old_paper() -> None:
    """At identical RRF / title match, a current-year paper should edge
    out a 5-year-old paper via the recency boost."""
    service = DedupService()
    current_year = datetime.now(UTC).year
    records = [
        _build_raw_record(
            source_id="older",
            source=SourceType.PUBMED,
            title="Cardiology review",
            year=current_year - 5,
            doi="10.1/older",
            source_rank=1,
        ),
        _build_raw_record(
            source_id="fresh",
            source=SourceType.PUBMED,
            title="Cardiology review",
            year=current_year,
            doi="10.1/fresh",
            source_rank=1,
        ),
    ]

    deduped = service.deduplicate(
        records,
        query="cardiology review",
        query_type=QueryType.FREE,
    )

    assert deduped[0].year == current_year


def test_rrf_tiebreaker_is_deterministic_across_repeated_runs() -> None:
    """Two clusters with identical fused scores must land in the same order
    every run. Sort keys cascade: score -> year -> source-count -> source
    value -> winner source_id. Two disjoint single-record clusters of the
    same PubMed source, year and rank differ only on source_id."""
    service = DedupService()
    base_records = [
        _build_raw_record(
            source_id="zzz-source",
            source=SourceType.PUBMED,
            title="Genomic biomarkers in oncology",
            year=2022,
            doi="10.1/zzz",
            source_rank=1,
        ),
        _build_raw_record(
            source_id="aaa-source",
            source=SourceType.PUBMED,
            title="Renal function tests in diabetes",
            year=2022,
            doi="10.1/aaa",
            source_rank=1,
        ),
    ]

    first_run = service.deduplicate(
        [record.model_copy() for record in base_records],
        query="study",
        query_type=QueryType.FREE,
    )
    second_run = service.deduplicate(
        [record.model_copy() for record in base_records],
        query="study",
        query_type=QueryType.FREE,
    )

    assert len(first_run) == 2
    assert len(second_run) == 2

    # Order is deterministic run-over-run.
    assert [record.doi for record in first_run] == [record.doi for record in second_run]

    # "aaa-source" < "zzz-source" lexicographically, so the "aaa" DOI wins
    # the final tiebreaker.
    assert first_run[0].doi == "10.1/aaa"
    assert first_run[1].doi == "10.1/zzz"


def test_rrf_falls_back_when_every_record_is_unranked() -> None:
    """Legacy records with ``source_rank == 0`` must not be re-ordered —
    the dedup service must detect this and keep first-seen ordering even
    when a query is supplied."""
    service = DedupService()
    records = [
        _build_raw_record(
            source_id="unranked-2",
            source=SourceType.PUBMED,
            title="Second legacy record",
            year=2021,
            doi="10.1/unranked.b",
        ),
        _build_raw_record(
            source_id="unranked-1",
            source=SourceType.PUBMED,
            title="First legacy record",
            year=2024,
            doi="10.1/unranked.a",
        ),
    ]

    deduped = service.deduplicate(
        records,
        query="legacy record",
        query_type=QueryType.FREE,
    )

    assert [record.title for record in deduped] == [
        "Second legacy record",
        "First legacy record",
    ]


def test_rrf_disabled_via_zero_boost_settings_still_sorts_by_rrf() -> None:
    """Setting both title/recency boosts to 0 reduces the algorithm to
    plain weighted RRF — still better than first-seen, still stable."""
    from src.core.config import get_settings

    settings = get_settings().model_copy(
        update={
            "RANKING_TITLE_BOOST": 0.0,
            "RANKING_RECENCY_BOOST": 0.0,
        }
    )
    service = DedupService(settings=settings)

    records = [
        _build_raw_record(
            source_id="rank-5",
            source=SourceType.PUBMED,
            title="Less relevant paper",
            year=2022,
            doi="10.1/r5",
            source_rank=5,
        ),
        _build_raw_record(
            source_id="rank-1",
            source=SourceType.PUBMED,
            title="Most relevant paper",
            year=2022,
            doi="10.1/r1",
            source_rank=1,
        ),
    ]

    deduped = service.deduplicate(
        records,
        query="relevant paper",
        query_type=QueryType.FREE,
    )

    assert deduped[0].title == "Most relevant paper"


# ── Phase 3 — MMR diversification ─────────────────────────────────
#
# MMR is *optional* and must default to a pure no-op when disabled
# (RANKING_MMR_LAMBDA >= 1.0). When enabled, it must only shuffle the
# top-K clusters and always preserve the #1 relevance hit at position 0.


def test_mmr_disabled_by_default_preserves_rrf_order() -> None:
    """With the default settings (lambda=1.0) MMR must be a no-op so
    the existing Phase 2 ordering is byte-identical to before."""
    service = DedupService()
    records = [
        _build_raw_record(
            source_id="a",
            source=SourceType.PUBMED,
            title="GLP-1 agonists and glycemic control",
            year=2023,
            doi="10.1/a",
            source_rank=1,
        ),
        _build_raw_record(
            source_id="b",
            source=SourceType.PUBMED,
            title="GLP-1 agonists and glycemic outcomes",
            year=2023,
            doi="10.1/b",
            source_rank=2,
        ),
        _build_raw_record(
            source_id="c",
            source=SourceType.PUBMED,
            title="GLP-1 agonists safety profile",
            year=2023,
            doi="10.1/c",
            source_rank=3,
        ),
    ]

    deduped = service.deduplicate(
        records,
        query="GLP-1 agonists",
        query_type=QueryType.FREE,
    )

    assert [record.doi for record in deduped] == ["10.1/a", "10.1/b", "10.1/c"]


def test_mmr_enabled_diversifies_near_duplicate_titles() -> None:
    """With MMR enabled (lambda=0.5) three papers with almost-identical
    titles should not all cluster at the top — the third slot goes to
    the paper with the most distinct title."""
    from src.core.config import get_settings

    settings = get_settings().model_copy(
        update={"RANKING_MMR_LAMBDA": 0.5, "RANKING_MMR_K": 3},
    )
    service = DedupService(settings=settings)

    # Three near-duplicate titles and one distinct title. All get the same
    # PubMed rank so fused scores are close; MMR should promote the outlier.
    records = [
        _build_raw_record(
            source_id="near-1",
            source=SourceType.PUBMED,
            title="metformin cardiovascular outcomes trial",
            year=2023,
            doi="10.1/n1",
            source_rank=1,
        ),
        _build_raw_record(
            source_id="near-2",
            source=SourceType.PUBMED,
            title="metformin cardiovascular outcomes review",
            year=2023,
            doi="10.1/n2",
            source_rank=2,
        ),
        _build_raw_record(
            source_id="near-3",
            source=SourceType.PUBMED,
            title="metformin cardiovascular outcomes meta-analysis",
            year=2023,
            doi="10.1/n3",
            source_rank=3,
        ),
        _build_raw_record(
            source_id="distinct",
            source=SourceType.PUBMED,
            title="glucose variability neuropathy progression",
            year=2023,
            doi="10.1/d",
            source_rank=4,
        ),
    ]

    deduped = service.deduplicate(
        records,
        query="metformin cardiovascular outcomes",
        query_type=QueryType.FREE,
    )

    dois = [record.doi for record in deduped]

    # The #1 relevance hit must stay at position 0 no matter what.
    assert dois[0] == "10.1/n1"

    # Without MMR, the distinct paper would end up last (rank-4). With MMR
    # diversification, it should be promoted above at least one of its
    # near-duplicate rivals.
    distinct_index = dois.index("10.1/d")
    assert distinct_index < 3


def test_mmr_only_reranks_top_k_long_tail_unchanged() -> None:
    """Clusters beyond RANKING_MMR_K stay in pure relevance order —
    the diversification pass only touches the first K slots."""
    from src.core.config import get_settings

    settings = get_settings().model_copy(
        update={"RANKING_MMR_LAMBDA": 0.5, "RANKING_MMR_K": 2},
    )
    service = DedupService(settings=settings)

    records = [
        _build_raw_record(
            source_id=f"rec-{index}",
            source=SourceType.PUBMED,
            title=f"Unique clinical study number {index}",
            year=2023,
            doi=f"10.1/s{index}",
            source_rank=index + 1,
        )
        for index in range(5)
    ]

    deduped = service.deduplicate(
        records,
        query="clinical study",
        query_type=QueryType.FREE,
    )

    # Positions past K=2 must retain their relevance ranks 3..5 in order.
    tail_dois = [record.doi for record in deduped[2:]]
    assert tail_dois == ["10.1/s2", "10.1/s3", "10.1/s4"]


def test_mmr_skipped_for_boolean_query_type() -> None:
    """MMR rides on top of RRF; BOOLEAN queries bypass RRF for PRISMA
    reproducibility, so MMR must not run either."""
    from src.core.config import get_settings

    settings = get_settings().model_copy(
        update={"RANKING_MMR_LAMBDA": 0.1, "RANKING_MMR_K": 5},
    )
    service = DedupService(settings=settings)

    records = [
        _build_raw_record(
            source_id="b-1",
            source=SourceType.PUBMED,
            title="Systematic review protocol one",
            year=2023,
            doi="10.1/b1",
            source_rank=1,
        ),
        _build_raw_record(
            source_id="b-2",
            source=SourceType.PUBMED,
            title="Systematic review protocol two",
            year=2023,
            doi="10.1/b2",
            source_rank=2,
        ),
    ]

    deduped = service.deduplicate(
        records,
        query="systematic review protocol",
        query_type=QueryType.BOOLEAN,
    )

    # First-seen order is preserved because BOOLEAN skips RRF+MMR entirely.
    assert [record.doi for record in deduped] == ["10.1/b1", "10.1/b2"]
