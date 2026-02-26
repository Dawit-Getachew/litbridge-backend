"""PRISMA filter and count schemas."""

from pydantic import BaseModel

from src.schemas.enums import SourceType


class PrismaFilters(BaseModel):
    """Optional filters applied when computing PRISMA counts."""

    year_from: int | None = None
    year_to: int | None = None
    sources: list[SourceType] | None = None
    open_access_only: bool = False


class PrismaCounts(BaseModel):
    """PRISMA flow diagram count metrics."""

    identified: int
    after_deduplication: int
    screened: int
    excluded: int
    oa_retrieved: int
