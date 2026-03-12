"""FastAPI router for Research Collections management (required auth)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from src.core.deps import get_current_user, get_research_collection_service
from src.core.exceptions import LitBridgeError
from src.models.user import User
from src.schemas.research_collection import (
    AddRecordsRequest,
    CollectionDetailResponse,
    CollectionItemResponse,
    CollectionListResponse,
    CollectionResponse,
    CreateCollectionRequest,
    MoveRecordRequest,
    UpdateCollectionRequest,
)
from src.services.research_collection_service import (
    CollectionAccessDeniedError,
    CollectionNotFoundError,
    ResearchCollectionService,
)

router = APIRouter(prefix="/collections", tags=["Research Collections"])


def _map_collection_error(exc: LitBridgeError) -> HTTPException:
    if isinstance(exc, CollectionNotFoundError):
        return HTTPException(status_code=404, detail=exc.message)
    if isinstance(exc, CollectionAccessDeniedError):
        return HTTPException(status_code=403, detail=exc.message)
    return HTTPException(status_code=400, detail=exc.message)


# -- Collection CRUD ----------------------------------------------------------

@router.get("", response_model=CollectionListResponse)
async def list_collections(
    user: User = Depends(get_current_user),
    service: ResearchCollectionService = Depends(get_research_collection_service),
) -> CollectionListResponse:
    """Return all research collections for the authenticated user."""

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
    user: User = Depends(get_current_user),
    service: ResearchCollectionService = Depends(get_research_collection_service),
) -> list[CollectionItemResponse]:
    """Add one or more records to a research collection."""

    try:
        return await service.add_records(collection_id, user.id, payload)
    except LitBridgeError as exc:
        raise _map_collection_error(exc) from exc


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
