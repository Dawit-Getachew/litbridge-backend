"""Business logic for the Research Collections feature."""

from __future__ import annotations

from uuid import UUID

import structlog

from src.core.exceptions import LitBridgeError
from src.models.research_collection import ResearchCollection
from src.repositories.research_collection_repo import ResearchCollectionRepository
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

logger = structlog.get_logger(__name__)


class CollectionNotFoundError(LitBridgeError):
    def __init__(self, collection_id: UUID) -> None:
        super().__init__(f"Research collection '{collection_id}' not found")


class CollectionAccessDeniedError(LitBridgeError):
    def __init__(self) -> None:
        super().__init__("You do not own this research collection")


class ResearchCollectionService:
    def __init__(self, repo: ResearchCollectionRepository) -> None:
        self.repo = repo

    # -- Helpers --------------------------------------------------------------

    async def _get_owned_collection(
        self, collection_id: UUID, user_id: UUID,
    ) -> ResearchCollection:
        collection = await self.repo.get_collection(collection_id)
        if collection is None:
            raise CollectionNotFoundError(collection_id)
        if collection.user_id != user_id:
            raise CollectionAccessDeniedError()
        return collection

    @staticmethod
    def _to_response(
        collection: ResearchCollection, item_count: int = 0,
    ) -> CollectionResponse:
        return CollectionResponse(
            id=collection.id,
            name=collection.name,
            description=collection.description,
            icon=collection.icon,
            color=collection.color,
            position=collection.position,
            item_count=item_count,
            created_at=collection.created_at,
            updated_at=collection.updated_at,
        )

    @staticmethod
    def _to_detail(
        collection: ResearchCollection, item_count: int,
    ) -> CollectionDetailResponse:
        return CollectionDetailResponse(
            id=collection.id,
            name=collection.name,
            description=collection.description,
            icon=collection.icon,
            color=collection.color,
            position=collection.position,
            item_count=item_count,
            created_at=collection.created_at,
            updated_at=collection.updated_at,
            items=[
                CollectionItemResponse(
                    id=it.id,
                    collection_id=it.collection_id,
                    record_id=it.record_id,
                    search_session_id=it.search_session_id,
                    title=it.title,
                    notes=it.notes,
                    created_at=it.created_at,
                )
                for it in (collection.items or [])
            ],
        )

    # -- CRUD -----------------------------------------------------------------

    async def list_collections(self, user_id: UUID) -> CollectionListResponse:
        collections = await self.repo.list_user_collections(user_id)
        counts = await self.repo.count_items_per_collection(user_id)
        return CollectionListResponse(
            collections=[
                self._to_response(c, counts.get(c.id, 0)) for c in collections
            ],
        )

    async def create_collection(
        self, user_id: UUID, payload: CreateCollectionRequest,
    ) -> CollectionResponse:
        collection = await self.repo.create_collection(
            user_id=user_id,
            name=payload.name,
            description=payload.description,
            icon=payload.icon,
            color=payload.color,
        )
        return self._to_response(collection, item_count=0)

    async def get_collection(
        self, collection_id: UUID, user_id: UUID,
    ) -> CollectionDetailResponse:
        collection = await self._get_owned_collection(collection_id, user_id)
        counts = await self.repo.count_items_per_collection(user_id)
        return self._to_detail(collection, counts.get(collection.id, 0))

    async def update_collection(
        self,
        collection_id: UUID,
        user_id: UUID,
        payload: UpdateCollectionRequest,
    ) -> CollectionResponse:
        collection = await self._get_owned_collection(collection_id, user_id)

        if payload.name is not None:
            collection.name = payload.name
        if payload.description is not None:
            collection.description = payload.description
        if payload.icon is not None:
            collection.icon = payload.icon
        if payload.color is not None:
            collection.color = payload.color
        if payload.position is not None:
            collection.position = payload.position

        await self.repo.update_collection(collection)
        counts = await self.repo.count_items_per_collection(user_id)
        return self._to_response(collection, counts.get(collection.id, 0))

    async def delete_collection(
        self, collection_id: UUID, user_id: UUID,
    ) -> None:
        collection = await self._get_owned_collection(collection_id, user_id)
        await self.repo.delete_collection(collection)

    # -- Records --------------------------------------------------------------

    async def add_records(
        self,
        collection_id: UUID,
        user_id: UUID,
        payload: AddRecordsRequest,
    ) -> list[CollectionItemResponse]:
        await self._get_owned_collection(collection_id, user_id)

        results: list[CollectionItemResponse] = []
        for record in payload.records:
            existing = await self.repo.get_item(collection_id, record.record_id)
            if existing:
                continue
            item = await self.repo.add_item(
                collection_id=collection_id,
                record_id=record.record_id,
                search_session_id=record.search_session_id,
                title=record.title,
                notes=record.notes,
            )
            results.append(
                CollectionItemResponse(
                    id=item.id,
                    collection_id=item.collection_id,
                    record_id=item.record_id,
                    search_session_id=item.search_session_id,
                    title=item.title,
                    notes=item.notes,
                    created_at=item.created_at,
                ),
            )
        return results

    async def remove_record(
        self,
        collection_id: UUID,
        record_id: str,
        user_id: UUID,
    ) -> None:
        await self._get_owned_collection(collection_id, user_id)
        removed = await self.repo.remove_item(collection_id, record_id)
        if not removed:
            raise LitBridgeError("Record not found in this collection")

    async def move_record(
        self,
        collection_id: UUID,
        record_id: str,
        user_id: UUID,
        payload: MoveRecordRequest,
    ) -> CollectionItemResponse:
        await self._get_owned_collection(collection_id, user_id)
        await self._get_owned_collection(payload.target_collection_id, user_id)

        existing = await self.repo.get_item(
            payload.target_collection_id, record_id,
        )
        if existing:
            raise LitBridgeError(
                "Record already exists in the target collection",
            )

        new_item = await self.repo.move_item(
            source_collection_id=collection_id,
            target_collection_id=payload.target_collection_id,
            record_id=record_id,
        )
        if new_item is None:
            raise LitBridgeError("Record not found in the source collection")

        return CollectionItemResponse(
            id=new_item.id,
            collection_id=new_item.collection_id,
            record_id=new_item.record_id,
            search_session_id=new_item.search_session_id,
            title=new_item.title,
            notes=new_item.notes,
            created_at=new_item.created_at,
        )
