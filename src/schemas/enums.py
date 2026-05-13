"""Enumeration schemas shared across API contracts."""

from enum import Enum


class SourceType(str, Enum):
    """External source identifiers for federated search."""

    PUBMED = "pubmed"
    EUROPEPMC = "europepmc"
    OPENALEX = "openalex"
    CLINICALTRIALS = "clinicaltrials"


class OAStatus(str, Enum):
    """Open-access status for a publication."""

    OPEN = "open"
    CLOSED = "closed"
    UNKNOWN = "unknown"


class QueryType(str, Enum):
    """Query interpretation mode."""

    FREE = "free"
    PICO = "structured"
    BOOLEAN = "boolean"
    ABSTRACT = "abstract"


class SearchMode(str, Enum):
    """Search depth and analysis mode."""

    QUICK = "quick"
    DEEP_RESEARCH = "deep_research"
    DEEP_ANALYZE = "deep_analyze"
    DEEP_THINKING = "deep_thinking"
    LIGHT_THINKING = "light_thinking"


class AgeGroup(str, Enum):
    """Standardised age-group categories aligned with ClinicalTrials.gov."""

    CHILD = "child"
    ADULT = "adult"
    OLDER_ADULT = "older_adult"


class StudyDesign(str, Enum):
    """Evidence-hierarchy classification for the LitPortal Study Design filter.

    Eight categories aligned to clinician decision-making. Records that cannot
    be confidently classified return ``None`` rather than falling into a
    default bucket; the frontend filter excludes ``None`` records.
    """

    GUIDELINE = "guideline"
    META_ANALYSIS = "meta_analysis"
    SYSTEMATIC_REVIEW = "systematic_review"
    RCT = "rct"
    REVIEW = "review"
    OBSERVATIONAL = "observational"
    CASE_REPORT = "case_report"
    EXPERT_OPINION = "expert_opinion"


STUDY_DESIGN_DISPLAY_LABELS: dict[StudyDesign, str] = {
    StudyDesign.GUIDELINE: "High Quality (Major Organization) Guideline",
    StudyDesign.META_ANALYSIS: "Meta Analysis",
    StudyDesign.SYSTEMATIC_REVIEW: "Systematic Literature Review",
    StudyDesign.RCT: "Randomized Controlled Trial",
    StudyDesign.REVIEW: "Review Article",
    StudyDesign.OBSERVATIONAL: "Observational Study",
    StudyDesign.CASE_REPORT: "Case Report",
    StudyDesign.EXPERT_OPINION: "Expert Opinion",
}
