"""Unit tests for the deterministic Study Design classifier.

Exercises every row of the mapping table from WEEK1_PLAN.md §5.4, the
tie-break rule (highest row wins), source-priority for cluster classification,
and the explicit-None contract (no defaulting on ambiguous input).
"""

from __future__ import annotations

import pytest

from src.schemas.enums import SourceType, StudyDesign
from src.schemas.records import RawRecord
from src.services.study_design_classifier import classify, classify_cluster


def _make_pubmed(publication_types: list[str], pmid: str = "1") -> RawRecord:
    return RawRecord(
        source_id=pmid,
        source=SourceType.PUBMED,
        title=f"PMID {pmid}",
        publication_types=publication_types,
        raw_data={"pmid": pmid, "publication_types": publication_types},
    )


def _make_europepmc(publication_types: list[str], src_id: str = "1") -> RawRecord:
    return RawRecord(
        source_id=src_id,
        source=SourceType.EUROPEPMC,
        title=f"EPMC {src_id}",
        publication_types=publication_types,
    )


def _make_openalex(types: list[str], src_id: str = "W1") -> RawRecord:
    return RawRecord(
        source_id=src_id,
        source=SourceType.OPENALEX,
        title=f"OA {src_id}",
        publication_types=types,
    )


def _make_ctgov(study_type: str | None, allocation: str | None, nct: str = "NCT00000001") -> RawRecord:
    design_module: dict = {}
    if study_type is not None:
        design_module["studyType"] = study_type
    if allocation is not None:
        design_module["designInfo"] = {"allocation": allocation}
    return RawRecord(
        source_id=nct,
        source=SourceType.CLINICALTRIALS,
        title=f"CT {nct}",
        raw_data={"protocolSection": {"designModule": design_module}},
    )


# -- One test per row of the mapping table -----------------------------------

@pytest.mark.parametrize(
    ("publication_types", "expected"),
    [
        (["Practice Guideline"], StudyDesign.GUIDELINE),
        (["Guideline"], StudyDesign.GUIDELINE),
        (["Consensus Development Conference (NIH)"], StudyDesign.GUIDELINE),
        (["Meta-Analysis"], StudyDesign.META_ANALYSIS),
        (["Systematic Review"], StudyDesign.SYSTEMATIC_REVIEW),
        (["Randomized Controlled Trial"], StudyDesign.RCT),
        (["Review"], StudyDesign.REVIEW),
        (["Observational Study"], StudyDesign.OBSERVATIONAL),
        (["Comparative Study"], StudyDesign.OBSERVATIONAL),
        (["Cohort Studies"], StudyDesign.OBSERVATIONAL),
        (["Case-Control Studies"], StudyDesign.OBSERVATIONAL),
        (["Cross-Sectional Studies"], StudyDesign.OBSERVATIONAL),
        (["Case Reports"], StudyDesign.CASE_REPORT),
        (["Editorial"], StudyDesign.EXPERT_OPINION),
        (["Letter"], StudyDesign.EXPERT_OPINION),
        (["Comment"], StudyDesign.EXPERT_OPINION),
    ],
)
def test_pubmed_trigger_table_row_by_row(
    publication_types: list[str], expected: StudyDesign
) -> None:
    assert classify(_make_pubmed(publication_types)) == expected


def test_case_insensitive_matching() -> None:
    assert classify(_make_pubmed(["RANDOMIZED CONTROLLED TRIAL"])) == StudyDesign.RCT
    assert classify(_make_pubmed(["meta-analysis"])) == StudyDesign.META_ANALYSIS


# -- Tie-break rule: higher row wins ------------------------------------------

def test_tiebreak_meta_analysis_wins_over_review() -> None:
    """A record carrying both 'Meta-Analysis' and 'Review' → meta_analysis."""
    record = _make_pubmed(["Meta-Analysis", "Review"])
    assert classify(record) == StudyDesign.META_ANALYSIS


def test_tiebreak_systematic_review_wins_over_review() -> None:
    record = _make_pubmed(["Systematic Review", "Review"])
    assert classify(record) == StudyDesign.SYSTEMATIC_REVIEW


def test_tiebreak_rct_wins_over_comparative_study() -> None:
    """RCT must NOT be downgraded to observational by Comparative Study tag."""
    record = _make_pubmed(["Randomized Controlled Trial", "Comparative Study"])
    assert classify(record) == StudyDesign.RCT


def test_tiebreak_guideline_wins_over_review() -> None:
    record = _make_pubmed(["Practice Guideline", "Review"])
    assert classify(record) == StudyDesign.GUIDELINE


# -- Explicit-None contract (no defaulting) -----------------------------------

def test_unknown_publication_type_returns_none() -> None:
    assert classify(_make_pubmed(["Journal Article"])) is None
    assert classify(_make_pubmed(["Research Support, U.S. Gov't, P.H.S."])) is None
    assert classify(_make_pubmed(["Some Future PublicationType"])) is None


def test_empty_publication_types_returns_none() -> None:
    assert classify(_make_pubmed([])) is None


def test_non_string_entries_ignored() -> None:
    # Defensive: bad data from a source shouldn't crash the classifier.
    record = RawRecord(
        source_id="1",
        source=SourceType.PUBMED,
        title="t",
        publication_types=[],
    )
    record.publication_types.extend([None, 123])  # type: ignore[list-item]
    assert classify(record) is None


# -- Europe PMC uses the same trigger table ----------------------------------

def test_europepmc_uses_pubmed_trigger_table() -> None:
    assert classify(_make_europepmc(["Meta-Analysis"])) == StudyDesign.META_ANALYSIS
    assert classify(_make_europepmc(["Case Reports"])) == StudyDesign.CASE_REPORT


# -- OpenAlex fallback map ---------------------------------------------------

@pytest.mark.parametrize(
    ("openalex_type", "expected"),
    [
        ("clinical-trial", StudyDesign.RCT),
        ("review", StudyDesign.REVIEW),
        ("editorial", StudyDesign.EXPERT_OPINION),
        ("letter", StudyDesign.EXPERT_OPINION),
    ],
)
def test_openalex_known_types(openalex_type: str, expected: StudyDesign) -> None:
    assert classify(_make_openalex([openalex_type])) == expected


def test_openalex_unknown_type_returns_none() -> None:
    assert classify(_make_openalex(["article"])) is None
    assert classify(_make_openalex(["book-chapter"])) is None
    assert classify(_make_openalex(["dataset"])) is None
    assert classify(_make_openalex(["preprint"])) is None


# -- ClinicalTrials.gov: only randomized interventional → RCT ----------------

def test_ctgov_randomized_interventional_is_rct() -> None:
    record = _make_ctgov(study_type="Interventional", allocation="Randomized")
    assert classify(record) == StudyDesign.RCT


def test_ctgov_non_randomized_interventional_is_none() -> None:
    record = _make_ctgov(study_type="Interventional", allocation="Non-Randomized")
    assert classify(record) is None


def test_ctgov_observational_protocol_is_none() -> None:
    """Observational CT.gov protocols are NOT papers and shouldn't be classified."""
    record = _make_ctgov(study_type="Observational", allocation=None)
    assert classify(record) is None


def test_ctgov_no_design_module_is_none() -> None:
    record = RawRecord(
        source_id="NCT2",
        source=SourceType.CLINICALTRIALS,
        title="t",
        raw_data={},
    )
    assert classify(record) is None


# -- Cluster classification: source-priority walk ----------------------------

def test_cluster_prefers_pubmed_over_openalex() -> None:
    cluster = [
        _make_openalex(["article"]),                  # OpenAlex → None
        _make_pubmed(["Randomized Controlled Trial"]),  # PubMed → RCT
    ]
    assert classify_cluster(cluster) == StudyDesign.RCT


def test_cluster_falls_through_to_europepmc_when_pubmed_silent() -> None:
    cluster = [
        _make_pubmed(["Journal Article"]),     # PubMed → None (Journal Article not a trigger)
        _make_europepmc(["Meta-Analysis"]),    # Europe PMC → meta_analysis
    ]
    assert classify_cluster(cluster) == StudyDesign.META_ANALYSIS


def test_cluster_falls_through_to_openalex_when_higher_sources_silent() -> None:
    cluster = [
        _make_pubmed([]),
        _make_europepmc([]),
        _make_openalex(["clinical-trial"]),
    ]
    assert classify_cluster(cluster) == StudyDesign.RCT


def test_cluster_falls_through_to_ctgov_last() -> None:
    cluster = [
        _make_pubmed([]),
        _make_ctgov(study_type="Interventional", allocation="Randomized"),
    ]
    assert classify_cluster(cluster) == StudyDesign.RCT


def test_cluster_all_unconfident_returns_none() -> None:
    cluster = [
        _make_pubmed(["Journal Article"]),
        _make_openalex(["article"]),
    ]
    assert classify_cluster(cluster) is None


def test_empty_cluster_returns_none() -> None:
    assert classify_cluster([]) is None
