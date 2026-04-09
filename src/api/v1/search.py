"""FastAPI router for search execution, status, and result pagination."""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from src.ai.adapters import translate_for_all_sources
from src.ai.llm_client import LLMClient
from src.api.v1.sse import stream_generator
from src.core.exceptions import RateLimitError, SearchNotFoundError, SourceFetchError
from src.core.deps import (
    get_current_user,
    get_current_user_optional,
    get_llm_client,
    get_search_service,
    get_streaming_search_service,
)
from src.models.user import User
from src.schemas.enums import QueryType
from src.schemas.records import PaginatedResults
from src.schemas.search import (
    SearchHistoryResponse,
    SearchRequest,
    SearchResponse,
    SearchStatusResponse,
)
from src.services.pico_fill_service import fill_missing_pico
from src.services.search_service import SearchService
from src.services.streaming_search_service import StreamingSearchService

router = APIRouter(tags=["Search"])


def _map_domain_error(exc: Exception) -> HTTPException:
    if isinstance(exc, SearchNotFoundError):
        return HTTPException(status_code=404, detail=exc.message)
    if isinstance(exc, SourceFetchError):
        return HTTPException(status_code=502, detail=exc.message)
    if isinstance(exc, RateLimitError):
        return HTTPException(
            status_code=429,
            detail=exc.message,
            headers={"Retry-After": str(exc.retry_after)},
        )
    raise exc


@router.post("/search", response_model=SearchResponse)
async def execute_search(
    request: SearchRequest,
    service: SearchService = Depends(get_search_service),
    user: User | None = Depends(get_current_user_optional),
) -> SearchResponse:
    """Run the fast-path search flow and return an opaque search_id."""
    try:
        return await service.execute_search(
            request, user_id=str(user.id) if user else None,
        )
    except (SearchNotFoundError, SourceFetchError, RateLimitError) as exc:
        raise _map_domain_error(exc) from exc


@router.post("/search/preview")
async def preview_search_query(
    request: SearchRequest,
    llm: LLMClient = Depends(get_llm_client),
    user: User | None = Depends(get_current_user_optional),
) -> dict[str, dict[str, str]]:
    """Return per-source translated query previews without executing search."""
    pico = request.pico
    if request.query_type is QueryType.PICO and pico is not None:
        pico = await fill_missing_pico(pico, llm)

    translated = await translate_for_all_sources(
        query=request.query,
        query_type=request.query_type,
        pico=pico,
        sources=request.sources,
    )
    return {"translations": {source.value: query for source, query in translated.items()}}


@router.post("/search/stream")
async def stream_search(
    request: SearchRequest,
    service: StreamingSearchService = Depends(get_streaming_search_service),
    user: User | None = Depends(get_current_user_optional),
) -> StreamingResponse:
    """Stream search progress as server-sent events."""
    return StreamingResponse(
        stream_generator(service.execute_search_stream(
            request, user_id=str(user.id) if user else None,
        )),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.get("/search/history", response_model=SearchHistoryResponse)
async def get_search_history(
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    service: SearchService = Depends(get_search_service),
    user: User = Depends(get_current_user),
) -> SearchHistoryResponse:
    """Return cursor-paginated search history for the authenticated user."""
    return await service.list_user_search_history(
        user_id=str(user.id),
        limit=limit,
        cursor=cursor,
    )


@router.get("/search/{search_id}/status", response_model=SearchStatusResponse)
async def get_search_status(
    search_id: str,
    service: SearchService = Depends(get_search_service),
    user: User | None = Depends(get_current_user_optional),
) -> SearchStatusResponse:
    """Return persisted status metadata for a search session."""
    try:
        return await service.get_search_status(search_id)
    except (SearchNotFoundError, SourceFetchError, RateLimitError) as exc:
        raise _map_domain_error(exc) from exc


@router.get("/results/{search_id}", response_model=PaginatedResults)
async def get_results_page(
    search_id: str,
    cursor: str | None = Query(default=None),
    service: SearchService = Depends(get_search_service),
    user: User | None = Depends(get_current_user_optional),
) -> PaginatedResults:
    """Return one cursor-paginated results page for a completed search."""
    try:
        return await service.get_results(search_id, cursor)
    except (SearchNotFoundError, SourceFetchError, RateLimitError) as exc:
        raise _map_domain_error(exc) from exc
