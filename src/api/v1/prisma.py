"""FastAPI router for PRISMA counts endpoints."""

from __future__ import annotations

from enum import Enum
from typing import TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query

from src.core.deps import get_current_user_optional, get_prisma_service, get_search_repo
from src.core.exceptions import SearchNotFoundError
from src.models.user import User
from src.repositories.search_repo import SearchRepository
from src.schemas.enums import AgeGroup, SourceType, StudyType
from src.schemas.prisma import PrismaCounts, PrismaFilters
from src.schemas.records import UnifiedRecord
from src.services.prisma_service import PrismaService

router = APIRouter(prefix="/prisma", tags=["Prisma"])

_E = TypeVar("_E", bound=Enum)


def _parse_enum_list(raw: str | None, enum_cls: type[_E], label: str) -> list[_E] | None:
    """Parse a comma-separated query param into a list of enum values."""
    if raw is None:
        return None

    parsed: list[_E] = []
    for value in raw.split(","):
        cleaned = value.strip().lower()
        if not cleaned:
            continue
        try:
            parsed.append(enum_cls(cleaned))
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid {label} '{cleaned}'.",
            ) from exc
    return parsed or None


@router.get("/{search_id}", response_model=PrismaCounts)
async def get_prisma_counts(
    search_id: str,
    year_from: int | None = Query(default=None),
    year_to: int | None = Query(default=None),
    sources: str | None = Query(default=None),
    open_access_only: bool = Query(default=False),
    age_group: str | None = Query(default=None),
    age_min: int | None = Query(default=None),
    age_max: int | None = Query(default=None),
    study_type: str | None = Query(default=None),
    search_repo: SearchRepository = Depends(get_search_repo),
    prisma_service: PrismaService = Depends(get_prisma_service),
    user: User | None = Depends(get_current_user_optional),
) -> PrismaCounts:
    """Compute PRISMA counts for one search with optional filters."""
    session = await search_repo.get_session(search_id)
    if session is None:
        error = SearchNotFoundError(search_id)
        raise HTTPException(status_code=404, detail=error.message)

    records = [UnifiedRecord.model_validate(item) for item in (session.results or [])]
    filters = PrismaFilters(
        year_from=year_from,
        year_to=year_to,
        sources=_parse_enum_list(sources, SourceType, "source"),
        open_access_only=open_access_only,
        age_groups=_parse_enum_list(age_group, AgeGroup, "age_group"),
        age_min=age_min,
        age_max=age_max,
        study_types=_parse_enum_list(study_type, StudyType, "study_type"),
    )
    identified = session.total_identified or len(records)
    return prisma_service.compute_counts(
        identified=identified,
        records=records,
        filters=filters,
    )
