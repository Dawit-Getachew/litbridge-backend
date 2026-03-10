"""FastAPI router for record-level enrichment retrieval."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from redis.asyncio import Redis

from src.core.deps import get_current_user_optional, get_enrichment_service, get_oa_service, get_redis, get_search_repo
from src.core.exceptions import SearchNotFoundError
from src.models.user import User
from src.core.redis import build_cache_key
from src.repositories.search_repo import SearchRepository
from src.schemas.enrichment import EnrichmentResponse
from src.schemas.records import UnifiedRecord
from src.services.enrichment_service import EnrichmentService
from src.services.oa_service import OAService

router = APIRouter(prefix="/enrichment", tags=["Enrichment"])


def _find_record(results: list[dict] | None, record_id: str) -> UnifiedRecord | None:
    for item in results or []:
        if not isinstance(item, dict):
            continue
        if item.get("id") != record_id:
            continue
        try:
            return UnifiedRecord.model_validate(item)
        except Exception:
            return None
    return None


@router.get("/{search_id}/{record_id}", response_model=EnrichmentResponse)
async def get_enrichment(
    search_id: str,
    record_id: str,
    search_repo: SearchRepository = Depends(get_search_repo),
    enrichment_service: EnrichmentService = Depends(get_enrichment_service),
    oa_service: OAService = Depends(get_oa_service),
    redis_client: Redis = Depends(get_redis),
    user: User | None = Depends(get_current_user_optional),
) -> EnrichmentResponse:
    """Return enrichment payload for one search result record."""
    session = await search_repo.get_session(search_id)
    if session is None:
        error = SearchNotFoundError(search_id)
        raise HTTPException(status_code=404, detail=error.message)

    record = _find_record(session.results, record_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"Record '{record_id}' was not found in search '{search_id}'",
        )

    cache_key = build_cache_key("enrichment", record_id)
    response: EnrichmentResponse | None = None
    cached = await redis_client.get(cache_key)
    if cached:
        try:
            response = EnrichmentResponse.model_validate_json(cached)
        except Exception:
            response = None

    if response is None:
        response = await enrichment_service.enrich_record(record)

    oa_status, pdf_url = await oa_service.resolve_oa(record)
    payload = response.model_dump(mode="json")
    payload["oa_status"] = oa_status
    payload["pdf_url"] = pdf_url
    return EnrichmentResponse.model_validate(payload)
