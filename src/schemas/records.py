"""Record schemas for unified and internal publication representations."""

from typing import Any

from pydantic import BaseModel, Field

from src.schemas.enums import AgeGroup, OAStatus, SourceType, StudyType


class UnifiedRecord(BaseModel):
    """Canonical publication record returned to API clients."""

    id: str
    title: str
    authors: list[str]
    journal: str | None = None
    year: int | None = None
    doi: str | None = None
    pmid: str | None = None
    source: SourceType
    sources_found_in: list[SourceType] = Field(default_factory=list)
    tldr: str | None = None
    citation_count: int | None = None
    oa_status: OAStatus = OAStatus.UNKNOWN
    pdf_url: str | None = None
    abstract: str | None = None
    duplicate_cluster_id: str | None = None
    prisma_stage: str | None = None
    age_groups: list[AgeGroup] = Field(default_factory=list)
    age_min: int | None = None
    age_max: int | None = None
    study_type: StudyType | None = None


class PaginatedResults(BaseModel):
    """Cursor-paginated search results payload."""

    search_id: str
    total_count: int
    records: list[UnifiedRecord]
    next_cursor: str | None = None


class RawRecord(BaseModel):
    """Internal schema used between agents. Not exposed to API."""

    source_id: str
    source: SourceType
    title: str
    authors: list[str] = Field(default_factory=list)
    journal: str | None = None
    year: int | None = None
    doi: str | None = None
    pmid: str | None = None
    abstract: str | None = None
    pdf_url: str | None = None
    oa_status: OAStatus = OAStatus.UNKNOWN
    raw_data: dict[str, Any] = Field(default_factory=dict)
    # Internal-only ranking signal: 1-based position in the source's relevance-
    # sorted result list. 0 means "unranked" (e.g. legacy cached records). Not
    # exposed in UnifiedRecord / API responses.
    source_rank: int = 0
