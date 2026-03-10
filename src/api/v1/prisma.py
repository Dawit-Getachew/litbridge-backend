"""FastAPI router for PRISMA counts endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from src.core.deps import get_current_user_optional, get_prisma_service, get_search_repo
from src.core.exceptions import SearchNotFoundError
from src.models.user import User
from src.repositories.search_repo import SearchRepository
from src.schemas.enums import SourceType
from src.schemas.prisma import PrismaCounts, PrismaFilters
from src.schemas.records import UnifiedRecord
from src.services.prisma_service import PrismaService

router = APIRouter(prefix="/prisma", tags=["Prisma"])


def _parse_sources(raw_sources: str | None) -> list[SourceType] | None:
    if raw_sources is None:
        return None

    parsed_sources: list[SourceType] = []
    for value in raw_sources.split(","):
        cleaned = value.strip().lower()
        if not cleaned:
            continue
        try:
            parsed_sources.append(SourceType(cleaned))
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid source '{cleaned}'.",
            ) from exc
    return parsed_sources or None


@router.get("/{search_id}", response_model=PrismaCounts)
async def get_prisma_counts(
    search_id: str,
    year_from: int | None = Query(default=None),
    year_to: int | None = Query(default=None),
    sources: str | None = Query(default=None),
    open_access_only: bool = Query(default=False),
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
        sources=_parse_sources(sources),
        open_access_only=open_access_only,
    )
    identified = session.total_identified or len(records)
    return prisma_service.compute_counts(
        identified=identified,
        records=records,
        filters=filters,
    )
