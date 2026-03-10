"""Business logic for the Library/Collections feature."""

from __future__ import annotations

from uuid import UUID

import structlog

from src.core.exceptions import LitBridgeError
from src.models.library import Library
from src.repositories.library_repo import LibraryRepository
from src.repositories.search_repo import SearchRepository
from src.schemas.library import (
    AddItemsRequest,
    CreateLibraryRequest,
    LibraryDetailResponse,
    LibraryItemResponse,
    LibraryResponse,
    LibraryTreeResponse,
    SearchSessionBrief,
    UpdateLibraryRequest,
    UserSearchesResponse,
)

_MAX_NESTING_DEPTH = 2

logger = structlog.get_logger(__name__)


class LibraryNotFoundError(LitBridgeError):
    def __init__(self, library_id: UUID) -> None:
        super().__init__(f"Library '{library_id}' not found")


class LibraryAccessDeniedError(LitBridgeError):
    def __init__(self) -> None:
        super().__init__("You do not own this library")


class LibraryNestingError(LitBridgeError):
    def __init__(self) -> None:
        super().__init__(
            f"Maximum nesting depth of {_MAX_NESTING_DEPTH} exceeded"
        )


class LibraryService:
    def __init__(
        self,
        library_repo: LibraryRepository,
        search_repo: SearchRepository,
    ) -> None:
        self.library_repo = library_repo
        self.search_repo = search_repo

    # ── helpers ───────────────────────────────────────────────────

    async def _get_owned_library(self, library_id: UUID, user_id: UUID) -> Library:
        lib = await self.library_repo.get_library(library_id)
        if lib is None:
            raise LibraryNotFoundError(library_id)
        if lib.user_id != user_id:
            raise LibraryAccessDeniedError()
        return lib

    async def _nesting_depth(self, parent_id: UUID | None) -> int:
        """Walk up the parent chain to calculate depth (root = 1)."""
        depth = 0
        current = parent_id
        while current is not None:
            depth += 1
            parent = await self.library_repo.get_library(current)
            current = parent.parent_id if parent else None
        return depth

    def _to_response(self, lib: Library, item_count: int = 0) -> LibraryResponse:
        return LibraryResponse(
            id=lib.id,
            name=lib.name,
            description=lib.description,
            parent_id=lib.parent_id,
            icon=lib.icon,
            color=lib.color,
            position=lib.position,
            item_count=item_count,
            created_at=lib.created_at,
            updated_at=lib.updated_at,
        )

    def _to_detail(
        self,
        lib: Library,
        item_count: int,
        children_counts: dict[UUID, int],
    ) -> LibraryDetailResponse:
        return LibraryDetailResponse(
            id=lib.id,
            name=lib.name,
            description=lib.description,
            parent_id=lib.parent_id,
            icon=lib.icon,
            color=lib.color,
            position=lib.position,
            item_count=item_count,
            created_at=lib.created_at,
            updated_at=lib.updated_at,
            items=[
                LibraryItemResponse(
                    id=it.id,
                    library_id=it.library_id,
                    search_session_id=it.search_session_id,
                    notes=it.notes,
                    created_at=it.created_at,
                )
                for it in (lib.items or [])
            ],
            children=[
                self._to_response(ch, children_counts.get(ch.id, 0))
                for ch in (lib.children or [])
            ],
        )

    # ── CRUD ──────────────────────────────────────────────────────

    async def list_libraries(self, user_id: UUID) -> LibraryTreeResponse:
        all_libs = await self.library_repo.list_user_libraries(user_id)
        counts = await self.library_repo.count_items_per_library(user_id)

        children_map: dict[UUID | None, list[Library]] = {}
        for lib in all_libs:
            children_map.setdefault(lib.parent_id, []).append(lib)

        roots = children_map.get(None, [])

        tree: list[LibraryDetailResponse] = []
        for root in roots:
            root.children = children_map.get(root.id, [])
            tree.append(self._to_detail(root, counts.get(root.id, 0), counts))

        return LibraryTreeResponse(libraries=tree)

    async def create_library(
        self,
        user_id: UUID,
        payload: CreateLibraryRequest,
    ) -> LibraryResponse:
        if payload.parent_id:
            await self._get_owned_library(payload.parent_id, user_id)
            depth = await self._nesting_depth(payload.parent_id)
            if depth + 1 > _MAX_NESTING_DEPTH:
                raise LibraryNestingError()

        lib = await self.library_repo.create_library(
            user_id=user_id,
            name=payload.name,
            description=payload.description,
            parent_id=payload.parent_id,
            icon=payload.icon,
            color=payload.color,
        )
        return self._to_response(lib, item_count=0)

    async def get_library(
        self,
        library_id: UUID,
        user_id: UUID,
    ) -> LibraryDetailResponse:
        lib = await self._get_owned_library(library_id, user_id)
        counts = await self.library_repo.count_items_per_library(user_id)
        all_libs = await self.library_repo.list_user_libraries(user_id)
        lib.children = [ch for ch in all_libs if ch.parent_id == library_id]
        return self._to_detail(lib, counts.get(lib.id, 0), counts)

    async def update_library(
        self,
        library_id: UUID,
        user_id: UUID,
        payload: UpdateLibraryRequest,
    ) -> LibraryResponse:
        lib = await self._get_owned_library(library_id, user_id)

        if payload.parent_id is not None and payload.parent_id != lib.parent_id:
            if payload.parent_id == lib.id:
                raise LibraryNestingError()
            await self._get_owned_library(payload.parent_id, user_id)
            depth = await self._nesting_depth(payload.parent_id)
            if depth + 1 > _MAX_NESTING_DEPTH:
                raise LibraryNestingError()
            lib.parent_id = payload.parent_id

        if payload.name is not None:
            lib.name = payload.name
        if payload.description is not None:
            lib.description = payload.description
        if payload.icon is not None:
            lib.icon = payload.icon
        if payload.color is not None:
            lib.color = payload.color
        if payload.position is not None:
            lib.position = payload.position

        await self.library_repo.update_library(lib)
        counts = await self.library_repo.count_items_per_library(user_id)
        return self._to_response(lib, counts.get(lib.id, 0))

    async def delete_library(
        self,
        library_id: UUID,
        user_id: UUID,
    ) -> None:
        lib = await self._get_owned_library(library_id, user_id)
        await self.library_repo.delete_library(lib)

    # ── Items ─────────────────────────────────────────────────────

    async def add_items(
        self,
        library_id: UUID,
        user_id: UUID,
        payload: AddItemsRequest,
    ) -> list[LibraryItemResponse]:
        await self._get_owned_library(library_id, user_id)

        results: list[LibraryItemResponse] = []
        for sid in payload.search_session_ids:
            existing = await self.library_repo.get_item(library_id, sid)
            if existing:
                continue
            item = await self.library_repo.add_item(
                library_id=library_id,
                search_session_id=sid,
                notes=payload.notes,
            )
            results.append(
                LibraryItemResponse(
                    id=item.id,
                    library_id=item.library_id,
                    search_session_id=item.search_session_id,
                    notes=item.notes,
                    created_at=item.created_at,
                )
            )
        return results

    async def remove_item(
        self,
        library_id: UUID,
        search_session_id: UUID,
        user_id: UUID,
    ) -> None:
        await self._get_owned_library(library_id, user_id)
        removed = await self.library_repo.remove_item(library_id, search_session_id)
        if not removed:
            raise LitBridgeError("Item not found in this library")

    # ── User searches ─────────────────────────────────────────────

    async def list_user_searches(
        self,
        user_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> UserSearchesResponse:
        sessions = await self.search_repo.list_user_sessions(user_id, limit, offset)
        return UserSearchesResponse(
            searches=[
                SearchSessionBrief(
                    id=s.id,
                    query=s.query,
                    query_type=s.query_type,
                    search_mode=s.search_mode,
                    sources=s.sources or [],
                    status=s.status,
                    total_after_dedup=s.total_after_dedup,
                    created_at=s.created_at,
                )
                for s in sessions
            ],
            total=len(sessions),
        )

    async def list_unfiled_searches(
        self,
        user_id: UUID,
    ) -> UserSearchesResponse:
        session_ids = await self.library_repo.list_unfiled_session_ids(user_id)
        sessions = []
        for sid in session_ids:
            s = await self.search_repo.get_session(str(sid))
            if s and s.user_id == user_id:
                sessions.append(s)

        return UserSearchesResponse(
            searches=[
                SearchSessionBrief(
                    id=s.id,
                    query=s.query,
                    query_type=s.query_type,
                    search_mode=s.search_mode,
                    sources=s.sources or [],
                    status=s.status,
                    total_after_dedup=s.total_after_dedup,
                    created_at=s.created_at,
                )
                for s in sessions
            ],
            total=len(sessions),
        )
