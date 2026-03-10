"""Repository for Library and LibraryItem CRUD and hierarchy queries."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.library import Library, LibraryItem


class LibraryRepository:
    """Persist and query libraries and their items."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Library CRUD ──────────────────────────────────────────────

    async def create_library(
        self,
        user_id: UUID,
        name: str,
        description: str | None = None,
        parent_id: UUID | None = None,
        icon: str | None = None,
        color: str | None = None,
        position: int = 0,
    ) -> Library:
        lib = Library(
            user_id=user_id,
            name=name,
            description=description,
            parent_id=parent_id,
            icon=icon,
            color=color,
            position=position,
        )
        self.db.add(lib)
        await self.db.commit()
        await self.db.refresh(lib)
        return lib

    async def get_library(self, library_id: UUID) -> Library | None:
        stmt = (
            select(Library)
            .where(Library.id == library_id)
            .options(selectinload(Library.items))
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_user_libraries(self, user_id: UUID) -> list[Library]:
        """Return all libraries for a user ordered by position, with items loaded."""
        stmt = (
            select(Library)
            .where(Library.user_id == user_id)
            .order_by(Library.position, Library.created_at)
            .options(selectinload(Library.items))
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def update_library(self, library: Library) -> None:
        await self.db.commit()
        await self.db.refresh(library)

    async def delete_library(self, library: Library) -> None:
        await self.db.delete(library)
        await self.db.commit()

    # ── Item CRUD ─────────────────────────────────────────────────

    async def add_item(
        self,
        library_id: UUID,
        search_session_id: UUID,
        notes: str | None = None,
    ) -> LibraryItem:
        item = LibraryItem(
            library_id=library_id,
            search_session_id=search_session_id,
            notes=notes,
        )
        self.db.add(item)
        await self.db.commit()
        await self.db.refresh(item)
        return item

    async def get_item(
        self,
        library_id: UUID,
        search_session_id: UUID,
    ) -> LibraryItem | None:
        stmt = select(LibraryItem).where(
            LibraryItem.library_id == library_id,
            LibraryItem.search_session_id == search_session_id,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def remove_item(
        self,
        library_id: UUID,
        search_session_id: UUID,
    ) -> bool:
        stmt = delete(LibraryItem).where(
            LibraryItem.library_id == library_id,
            LibraryItem.search_session_id == search_session_id,
        )
        result = await self.db.execute(stmt)
        await self.db.commit()
        return result.rowcount > 0

    async def list_library_items(self, library_id: UUID) -> list[LibraryItem]:
        stmt = (
            select(LibraryItem)
            .where(LibraryItem.library_id == library_id)
            .order_by(LibraryItem.created_at.desc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    # ── Aggregate queries ─────────────────────────────────────────

    async def count_items_per_library(self, user_id: UUID) -> dict[UUID, int]:
        """Return {library_id: item_count} for all libraries owned by the user."""
        stmt = (
            select(LibraryItem.library_id, func.count(LibraryItem.id))
            .join(Library, LibraryItem.library_id == Library.id)
            .where(Library.user_id == user_id)
            .group_by(LibraryItem.library_id)
        )
        result = await self.db.execute(stmt)
        return {row[0]: row[1] for row in result.all()}

    async def list_unfiled_session_ids(self, user_id: UUID) -> list[UUID]:
        """Return search_session IDs owned by user that are not in any library."""
        from src.models.search import SearchSession

        filed_sub = select(LibraryItem.search_session_id).distinct()
        stmt = (
            select(SearchSession.id)
            .where(
                SearchSession.user_id == user_id,
                SearchSession.id.notin_(filed_sub),
            )
            .order_by(SearchSession.created_at.desc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())
