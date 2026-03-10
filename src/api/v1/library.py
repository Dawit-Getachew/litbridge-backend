"""FastAPI router for Library/Collections management (required auth)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from src.core.deps import get_current_user, get_library_service
from src.core.exceptions import LitBridgeError
from src.models.user import User
from src.schemas.library import (
    AddItemsRequest,
    CreateLibraryRequest,
    LibraryDetailResponse,
    LibraryItemResponse,
    LibraryResponse,
    LibraryTreeResponse,
    UpdateLibraryRequest,
    UserSearchesResponse,
)
from src.services.library_service import (
    LibraryAccessDeniedError,
    LibraryNestingError,
    LibraryNotFoundError,
    LibraryService,
)

router = APIRouter(prefix="/library", tags=["Library"])


def _map_library_error(exc: LitBridgeError) -> HTTPException:
    if isinstance(exc, LibraryNotFoundError):
        return HTTPException(status_code=404, detail=exc.message)
    if isinstance(exc, LibraryAccessDeniedError):
        return HTTPException(status_code=403, detail=exc.message)
    if isinstance(exc, LibraryNestingError):
        return HTTPException(status_code=422, detail=exc.message)
    return HTTPException(status_code=400, detail=exc.message)


# ── Library CRUD ──────────────────────────────────────────────────

@router.get("", response_model=LibraryTreeResponse)
async def list_libraries(
    user: User = Depends(get_current_user),
    service: LibraryService = Depends(get_library_service),
) -> LibraryTreeResponse:
    """Return the user's library tree with item counts."""
    return await service.list_libraries(user.id)


@router.post("", response_model=LibraryResponse, status_code=201)
async def create_library(
    payload: CreateLibraryRequest,
    user: User = Depends(get_current_user),
    service: LibraryService = Depends(get_library_service),
) -> LibraryResponse:
    """Create a new library/collection."""
    try:
        return await service.create_library(user.id, payload)
    except LitBridgeError as exc:
        raise _map_library_error(exc) from exc


@router.get("/searches", response_model=UserSearchesResponse)
async def list_user_searches(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    service: LibraryService = Depends(get_library_service),
) -> UserSearchesResponse:
    """List all searches belonging to the authenticated user."""
    return await service.list_user_searches(user.id, limit, offset)


@router.get("/searches/unfiled", response_model=UserSearchesResponse)
async def list_unfiled_searches(
    user: User = Depends(get_current_user),
    service: LibraryService = Depends(get_library_service),
) -> UserSearchesResponse:
    """List searches not in any library."""
    return await service.list_unfiled_searches(user.id)


@router.get("/{library_id}", response_model=LibraryDetailResponse)
async def get_library(
    library_id: UUID,
    user: User = Depends(get_current_user),
    service: LibraryService = Depends(get_library_service),
) -> LibraryDetailResponse:
    """Get a library with its items and children."""
    try:
        return await service.get_library(library_id, user.id)
    except LitBridgeError as exc:
        raise _map_library_error(exc) from exc


@router.patch("/{library_id}", response_model=LibraryResponse)
async def update_library(
    library_id: UUID,
    payload: UpdateLibraryRequest,
    user: User = Depends(get_current_user),
    service: LibraryService = Depends(get_library_service),
) -> LibraryResponse:
    """Rename, move, recolor, or reorder a library."""
    try:
        return await service.update_library(library_id, user.id, payload)
    except LitBridgeError as exc:
        raise _map_library_error(exc) from exc


@router.delete("/{library_id}", status_code=204)
async def delete_library(
    library_id: UUID,
    user: User = Depends(get_current_user),
    service: LibraryService = Depends(get_library_service),
) -> None:
    """Delete a library (items are unlinked, searches remain)."""
    try:
        await service.delete_library(library_id, user.id)
    except LitBridgeError as exc:
        raise _map_library_error(exc) from exc


# ── Library items ─────────────────────────────────────────────────

@router.post(
    "/{library_id}/items",
    response_model=list[LibraryItemResponse],
    status_code=201,
)
async def add_items(
    library_id: UUID,
    payload: AddItemsRequest,
    user: User = Depends(get_current_user),
    service: LibraryService = Depends(get_library_service),
) -> list[LibraryItemResponse]:
    """Add one or more searches to a library."""
    try:
        return await service.add_items(library_id, user.id, payload)
    except LitBridgeError as exc:
        raise _map_library_error(exc) from exc


@router.delete("/{library_id}/items/{search_id}", status_code=204)
async def remove_item(
    library_id: UUID,
    search_id: UUID,
    user: User = Depends(get_current_user),
    service: LibraryService = Depends(get_library_service),
) -> None:
    """Remove a search from a library."""
    try:
        await service.remove_item(library_id, search_id, user.id)
    except LitBridgeError as exc:
        raise _map_library_error(exc) from exc
