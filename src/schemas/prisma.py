"""PRISMA filter and count schemas."""

from pydantic import BaseModel

from src.schemas.enums import AgeGroup, SourceType, StudyType


class PrismaFilters(BaseModel):
    """Optional filters applied when computing PRISMA counts."""

    year_from: int | None = None
    year_to: int | None = None
    sources: list[SourceType] | None = None
    open_access_only: bool = False
    age_groups: list[AgeGroup] | None = None
    age_min: int | None = None
    age_max: int | None = None
    study_types: list[StudyType] | None = None


class PrismaCounts(BaseModel):
    """PRISMA flow diagram count metrics."""

    identified: int
    after_deduplication: int
    screened: int
    excluded: int
    oa_retrieved: int
