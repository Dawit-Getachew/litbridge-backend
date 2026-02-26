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
