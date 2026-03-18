"""FastAPI router for Research Collections management (required auth)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from src.ai.llm_client import LLMClient
from src.core.deps import (
    get_current_user,
    get_llm_client,
    get_paper_extraction_service,
    get_research_collection_service,
)
from src.core.exceptions import LitBridgeError
from src.models.user import User
from src.repositories.research_collection_repo import ResearchCollectionRepository
from src.repositories.search_repo import SearchRepository
from src.schemas.research_collection import (
    AddRecordsRequest,
    CollectionDetailResponse,
    CollectionItemResponse,
    CollectionResponse,
    CollectionTreeResponse,
    CreateCollectionRequest,
    MoveRecordRequest,
    PaperMetadata,
    UpdateCollectionRequest,
)
from src.services.paper_extraction_service import PaperExtractionService
from src.services.research_collection_service import (
    CollectionAccessDeniedError,
    CollectionNestingError,
    CollectionNotFoundError,
    ResearchCollectionService,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/collections", tags=["Research Collections"])


async def _run_extraction_background(
    items: list[dict[str, Any]],
    session_factory: Any,
    llm_client: LLMClient,
    redis_client: Any,
) -> None:
    """Run paper metadata extraction in a background task with its own DB session."""
    async with session_factory() as session:
        repo = ResearchCollectionRepository(db=session)
        search_repo = SearchRepository(db=session)
        svc = PaperExtractionService(
            llm_client=llm_client,
            redis_client=redis_client,
            repo=repo,
            search_repo=search_repo,
        )
        try:
            await svc.extract_batch(items)
        except Exception as exc:
            logger.warning("background_extraction_failed", error=str(exc))


def _map_collection_error(exc: LitBridgeError) -> HTTPException:
    if isinstance(exc, CollectionNotFoundError):
        return HTTPException(status_code=404, detail=exc.message)
    if isinstance(exc, CollectionAccessDeniedError):
        return HTTPException(status_code=403, detail=exc.message)
    if isinstance(exc, CollectionNestingError):
        return HTTPException(status_code=422, detail=exc.message)
    return HTTPException(status_code=400, detail=exc.message)


# -- Collection CRUD ----------------------------------------------------------

@router.get("", response_model=CollectionTreeResponse)
async def list_collections(
    user: User = Depends(get_current_user),
    service: ResearchCollectionService = Depends(get_research_collection_service),
) -> CollectionTreeResponse:
    """Return tree of root collections with nested children and items."""

    return await service.list_collections(user.id)


@router.post("", response_model=CollectionResponse, status_code=201)
async def create_collection(
    payload: CreateCollectionRequest,
    user: User = Depends(get_current_user),
    service: ResearchCollectionService = Depends(get_research_collection_service),
) -> CollectionResponse:
    """Create a new research collection."""

    return await service.create_collection(user.id, payload)


@router.get("/{collection_id}", response_model=CollectionDetailResponse)
async def get_collection(
    collection_id: UUID,
    user: User = Depends(get_current_user),
    service: ResearchCollectionService = Depends(get_research_collection_service),
) -> CollectionDetailResponse:
    """Get a research collection with its record items."""

    try:
        return await service.get_collection(collection_id, user.id)
    except LitBridgeError as exc:
        raise _map_collection_error(exc) from exc


@router.patch("/{collection_id}", response_model=CollectionResponse)
async def update_collection(
    collection_id: UUID,
    payload: UpdateCollectionRequest,
    user: User = Depends(get_current_user),
    service: ResearchCollectionService = Depends(get_research_collection_service),
) -> CollectionResponse:
    """Update a research collection's name, description, icon, color, or position."""

    try:
        return await service.update_collection(collection_id, user.id, payload)
    except LitBridgeError as exc:
        raise _map_collection_error(exc) from exc


@router.delete("/{collection_id}", status_code=204)
async def delete_collection(
    collection_id: UUID,
    user: User = Depends(get_current_user),
    service: ResearchCollectionService = Depends(get_research_collection_service),
) -> None:
    """Delete a research collection and all its items."""

    try:
        await service.delete_collection(collection_id, user.id)
    except LitBridgeError as exc:
        raise _map_collection_error(exc) from exc


# -- Record items -------------------------------------------------------------

@router.post(
    "/{collection_id}/records",
    response_model=list[CollectionItemResponse],
    status_code=201,
)
async def add_records(
    collection_id: UUID,
    payload: AddRecordsRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    service: ResearchCollectionService = Depends(get_research_collection_service),
    llm: LLMClient = Depends(get_llm_client),
) -> list[CollectionItemResponse]:
    """Add one or more records to a research collection.

    AI metadata extraction runs in the background with its own DB session.
    """
    try:
        items = await service.add_records(collection_id, user.id, payload)
    except LitBridgeError as exc:
        raise _map_collection_error(exc) from exc

    extraction_items = [
        {
            "item_id": item.id,
            "title": item.title or "",
            "record_id": item.record_id,
            "search_session_id": item.search_session_id,
        }
        for item in items
    ]
    if extraction_items:
        background_tasks.add_task(
            _run_extraction_background,
            extraction_items,
            request.app.state.db_session_factory,
            llm,
            request.app.state.redis,
        )

    return items


@router.delete("/{collection_id}/records/{record_id}", status_code=204)
async def remove_record(
    collection_id: UUID,
    record_id: str,
    user: User = Depends(get_current_user),
    service: ResearchCollectionService = Depends(get_research_collection_service),
) -> None:
    """Remove a record from a research collection."""

    try:
        await service.remove_record(collection_id, record_id, user.id)
    except LitBridgeError as exc:
        raise _map_collection_error(exc) from exc


@router.post(
    "/{collection_id}/records/{record_id}/move",
    response_model=CollectionItemResponse,
)
async def move_record(
    collection_id: UUID,
    record_id: str,
    payload: MoveRecordRequest,
    user: User = Depends(get_current_user),
    service: ResearchCollectionService = Depends(get_research_collection_service),
) -> CollectionItemResponse:
    """Move a record from this collection to another one."""

    try:
        return await service.move_record(
            collection_id, record_id, user.id, payload,
        )
    except LitBridgeError as exc:
        raise _map_collection_error(exc) from exc


@router.post(
    "/{collection_id}/records/{record_id}/extract",
    response_model=PaperMetadata,
)
async def extract_record_metadata(
    collection_id: UUID,
    record_id: str,
    user: User = Depends(get_current_user),
    service: ResearchCollectionService = Depends(get_research_collection_service),
    extraction: PaperExtractionService = Depends(get_paper_extraction_service),
) -> PaperMetadata:
    """Manually trigger AI metadata extraction (or re-extraction) for a record."""

    try:
        collection = await service.get_collection(collection_id, user.id)
    except LitBridgeError as exc:
        raise _map_collection_error(exc) from exc

    item = next(
        (it for it in collection.items if it.record_id == record_id),
        None,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Record not found in this collection")

    return await extraction.extract_and_persist(
        item_id=item.id,
        title=item.title or "",
        abstract=None,
        record_id=record_id,
        search_session_id=item.search_session_id,
    )
