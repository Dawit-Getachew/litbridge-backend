"""Repository for ResearchCollection and ResearchCollectionItem CRUD."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.research_collection import ResearchCollection, ResearchCollectionItem


class ResearchCollectionRepository:
    """Persist and query research collections and their record items."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # -- Collection CRUD ------------------------------------------------------

    async def create_collection(
        self,
        user_id: UUID,
        name: str,
        description: str | None = None,
        icon: str | None = None,
        color: str | None = None,
        position: int = 0,
    ) -> ResearchCollection:
        collection = ResearchCollection(
            user_id=user_id,
            name=name,
            description=description,
            icon=icon,
            color=color,
            position=position,
        )
        self.db.add(collection)
        await self.db.commit()
        await self.db.refresh(collection)
        return collection

    async def get_collection(self, collection_id: UUID) -> ResearchCollection | None:
        stmt = (
            select(ResearchCollection)
            .where(ResearchCollection.id == collection_id)
            .options(selectinload(ResearchCollection.items))
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_user_collections(self, user_id: UUID) -> list[ResearchCollection]:
        """Return all collections for a user ordered by position then created_at."""

        stmt = (
            select(ResearchCollection)
            .where(ResearchCollection.user_id == user_id)
            .order_by(ResearchCollection.position, ResearchCollection.created_at)
            .options(selectinload(ResearchCollection.items))
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def update_collection(self, collection: ResearchCollection) -> None:
        await self.db.commit()
        await self.db.refresh(collection)

    async def delete_collection(self, collection: ResearchCollection) -> None:
        await self.db.delete(collection)
        await self.db.commit()

    # -- Item CRUD ------------------------------------------------------------

    async def add_item(
        self,
        collection_id: UUID,
        record_id: str,
        search_session_id: UUID,
        title: str | None = None,
        notes: str | None = None,
    ) -> ResearchCollectionItem:
        item = ResearchCollectionItem(
            collection_id=collection_id,
            record_id=record_id,
            search_session_id=search_session_id,
            title=title,
            notes=notes,
        )
        self.db.add(item)
        await self.db.commit()
        await self.db.refresh(item)
        return item

    async def get_item(
        self,
        collection_id: UUID,
        record_id: str,
    ) -> ResearchCollectionItem | None:
        stmt = select(ResearchCollectionItem).where(
            ResearchCollectionItem.collection_id == collection_id,
            ResearchCollectionItem.record_id == record_id,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def remove_item(self, collection_id: UUID, record_id: str) -> bool:
        stmt = delete(ResearchCollectionItem).where(
            ResearchCollectionItem.collection_id == collection_id,
            ResearchCollectionItem.record_id == record_id,
        )
        result = await self.db.execute(stmt)
        await self.db.commit()
        return result.rowcount > 0

    async def move_item(
        self,
        source_collection_id: UUID,
        target_collection_id: UUID,
        record_id: str,
    ) -> ResearchCollectionItem | None:
        """Atomically move a record from one collection to another.

        Returns the newly created item in the target collection,
        or None if the record was not found in the source.
        """
        item = await self.get_item(source_collection_id, record_id)
        if item is None:
            return None

        search_session_id = item.search_session_id
        title = item.title
        notes = item.notes

        stmt = delete(ResearchCollectionItem).where(
            ResearchCollectionItem.collection_id == source_collection_id,
            ResearchCollectionItem.record_id == record_id,
        )
        await self.db.execute(stmt)

        new_item = ResearchCollectionItem(
            collection_id=target_collection_id,
            record_id=record_id,
            search_session_id=search_session_id,
            title=title,
            notes=notes,
        )
        self.db.add(new_item)
        await self.db.commit()
        await self.db.refresh(new_item)
        return new_item

    async def list_collection_items(
        self, collection_id: UUID,
    ) -> list[ResearchCollectionItem]:
        stmt = (
            select(ResearchCollectionItem)
            .where(ResearchCollectionItem.collection_id == collection_id)
            .order_by(ResearchCollectionItem.created_at.desc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    # -- Aggregate queries ----------------------------------------------------

    async def count_items_per_collection(self, user_id: UUID) -> dict[UUID, int]:
        """Return {collection_id: item_count} for all collections owned by the user."""

        stmt = (
            select(
                ResearchCollectionItem.collection_id,
                func.count(ResearchCollectionItem.id),
            )
            .join(
                ResearchCollection,
                ResearchCollectionItem.collection_id == ResearchCollection.id,
            )
            .where(ResearchCollection.user_id == user_id)
            .group_by(ResearchCollectionItem.collection_id)
        )
        result = await self.db.execute(stmt)
        return {row[0]: row[1] for row in result.all()}
