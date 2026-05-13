"""Deterministic Study Design classifier.

Maps the ``publication_types`` and source-specific structured metadata captured
on ``RawRecord`` into one of eight evidence-hierarchy categories. Records that
cannot be **confidently** classified return ``None`` — the LitPortal frontend
filter excludes ``None`` records rather than defaulting them into a bucket
(per the Phase 1.5 spec).

Source of truth for the mapping table: ``WEEK1_PLAN.md`` §5.4. Tie-break rule
is "highest row in the table wins" — so a record carrying both "Meta-Analysis"
and "Review" publication-type strings classifies as ``meta_analysis``, not
``review``.

This module is intentionally a pure function with no I/O — easy to unit-test
and identical behavior in every environment.
"""

from __future__ import annotations

from typing import Any

from src.schemas.enums import SourceType, StudyDesign
from src.schemas.records import RawRecord


# -- PubMed PublicationType triggers (case-insensitive ``contains`` match) ----
# Order matters: tie-break = first match wins, so place the higher-evidence
# categories first.

_PUBMED_TRIGGERS: list[tuple[StudyDesign, tuple[str, ...]]] = [
    (
        StudyDesign.GUIDELINE,
        ("practice guideline", "guideline", "consensus development conference"),
    ),
    (
        StudyDesign.META_ANALYSIS,
        ("meta-analysis", "meta analysis"),
    ),
    (
        StudyDesign.SYSTEMATIC_REVIEW,
        ("systematic review",),
    ),
    (
        StudyDesign.RCT,
        ("randomized controlled trial",),
    ),
    # "Review" must come AFTER systematic-review / meta-analysis above so those
    # don't get downgraded to plain narrative review.
    (
        StudyDesign.REVIEW,
        ("review",),
    ),
    (
        StudyDesign.OBSERVATIONAL,
        (
            "observational study",
            "comparative study",
            "cohort studies",
            "case-control studies",
            "cross-sectional studies",
        ),
    ),
    (
        StudyDesign.CASE_REPORT,
        ("case reports",),
    ),
    (
        StudyDesign.EXPERT_OPINION,
        ("editorial", "letter", "comment"),
    ),
]


# -- OpenAlex / Crossref ``type`` field overlay -------------------------------
# Only used when no PubMed PublicationType matched. OpenAlex doc-type metadata
# is noisier than PubMed's, so we keep this map conservative: a few clear
# mappings and ``None`` for everything else.

_OPENALEX_TYPE_MAP: dict[str, StudyDesign] = {
    "clinical-trial": StudyDesign.RCT,
    "review": StudyDesign.REVIEW,
    "editorial": StudyDesign.EXPERT_OPINION,
    "letter": StudyDesign.EXPERT_OPINION,
}


def _classify_from_publication_types(
    publication_types: list[str],
) -> StudyDesign | None:
    """Apply the PubMed-style trigger table to a list of publication-type strings."""
    if not publication_types:
        return None
    lowered = [pt.lower() for pt in publication_types if isinstance(pt, str)]
    if not lowered:
        return None
    for design, triggers in _PUBMED_TRIGGERS:
        for trigger in triggers:
            if any(trigger in pt for pt in lowered):
                return design
    return None


def _classify_openalex(publication_types: list[str]) -> StudyDesign | None:
    """Fallback for OpenAlex when its raw ``type`` field is the only signal."""
    for pt in publication_types:
        if not isinstance(pt, str):
            continue
        mapped = _OPENALEX_TYPE_MAP.get(pt.strip().lower())
        if mapped is not None:
            return mapped
    return None


def _classify_clinicaltrials(raw_data: dict[str, Any] | None) -> StudyDesign | None:
    """ClinicalTrials.gov: only confidently classify randomized interventional trials as RCT.

    All other CT.gov records return ``None``. CT.gov's "Interventional" study
    type alone is not sufficient — many interventional trials are single-arm
    or non-randomized. We require an explicit ``Randomized`` allocation to
    avoid mislabeling non-RCT trials.
    """
    if not isinstance(raw_data, dict):
        return None
    protocol = raw_data.get("protocolSection")
    if not isinstance(protocol, dict):
        return None
    design = protocol.get("designModule")
    if not isinstance(design, dict):
        return None
    study_type = design.get("studyType")
    if not isinstance(study_type, str) or study_type.strip().lower() != "interventional":
        return None
    design_info = design.get("designInfo")
    if not isinstance(design_info, dict):
        return None
    allocation = design_info.get("allocation")
    if isinstance(allocation, str) and allocation.strip().lower() == "randomized":
        return StudyDesign.RCT
    return None


def classify(record: RawRecord) -> StudyDesign | None:
    """Classify a single ``RawRecord`` into one of 8 Study Design categories.

    Returns ``None`` when no confident classification is possible.
    """
    # PubMed and Europe PMC both supply the canonical NCBI PublicationType
    # vocabulary on the ``publication_types`` list — same trigger table applies.
    if record.source in (SourceType.PUBMED, SourceType.EUROPEPMC):
        return _classify_from_publication_types(record.publication_types)

    if record.source is SourceType.OPENALEX:
        # OpenAlex doesn't supply PubMed PublicationType strings directly; its
        # ``type`` field uses a small Crossref-derived vocabulary.
        return _classify_openalex(record.publication_types)

    if record.source is SourceType.CLINICALTRIALS:
        return _classify_clinicaltrials(record.raw_data)

    return None


# -- Source priority for cluster-aware classification -------------------------
# PubMed is the canonical source for PublicationType metadata. Europe PMC also
# provides NCBI-aligned pubTypes but is sometimes thinner. OpenAlex is noisy.
# CT.gov is protocol-only.
_SOURCE_PRIORITY: tuple[SourceType, ...] = (
    SourceType.PUBMED,
    SourceType.EUROPEPMC,
    SourceType.OPENALEX,
    SourceType.CLINICALTRIALS,
)


def classify_cluster(cluster: list[RawRecord]) -> StudyDesign | None:
    """Classify a dedup cluster by walking source-by-source in priority order.

    Returns the first confident classification found. If every source in the
    cluster yields ``None``, returns ``None`` — the record is excluded from
    the Study Design filter rather than defaulted.
    """
    if not cluster:
        return None
    by_source: dict[SourceType, list[RawRecord]] = {}
    for record in cluster:
        by_source.setdefault(record.source, []).append(record)
    for source in _SOURCE_PRIORITY:
        for record in by_source.get(source, []):
            result = classify(record)
            if result is not None:
                return result
    return None
