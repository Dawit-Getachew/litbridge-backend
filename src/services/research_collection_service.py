"""Business logic for the Research Collections feature."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog

from src.core.exceptions import LitBridgeError
from src.models.research_collection import ResearchCollection, ResearchCollectionItem
from src.repositories.research_collection_repo import ResearchCollectionRepository
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

if TYPE_CHECKING:
    from src.clients.lithub_client import LitHubClient

logger = structlog.get_logger(__name__)

_MAX_NESTING_DEPTH = 2
_DEFAULT_METADATA = PaperMetadata()


def _merge_lithub_metadata(metadata: PaperMetadata, paper: dict[str, Any]) -> PaperMetadata:
    """Fill still-empty PaperMetadata fields from a canonical LitHub paper.

    Local LLM-extracted metadata always wins; LitHub only backfills fields that
    are still at their default so the cross-app view is non-empty even before
    LLM extraction has run. The PaperMetadata shape is preserved exactly.
    """
    data = metadata.model_dump()
    changed = False
    if not data.get("study_design") and paper.get("study_design"):
        data["study_design"] = paper["study_design"]
        changed = True
    if data.get("key_findings", "Not reported") in (None, "", "Not reported") and paper.get("ai_summary"):
        data["key_findings"] = str(paper["ai_summary"])[:2000]
        changed = True
    if data.get("study_details", "Not reported") in (None, "", "Not reported"):
        bits = [paper.get("journal"), paper.get("pub_date")]
        joined = " · ".join(str(b) for b in bits if b)
        if joined:
            data["study_details"] = joined
            changed = True
    if not changed:
        return metadata
    try:
        return PaperMetadata.model_validate(data)
    except Exception:
        return metadata


class CollectionNotFoundError(LitBridgeError):
    def __init__(self, collection_id: UUID) -> None:
        super().__init__(f"Research collection '{collection_id}' not found")


class CollectionAccessDeniedError(LitBridgeError):
    def __init__(self) -> None:
        super().__init__("You do not own this research collection")


class CollectionNestingError(LitBridgeError):
    def __init__(self) -> None:
        super().__init__(f"Maximum folder nesting depth of {_MAX_NESTING_DEPTH} exceeded")


class ResearchCollectionService:
    def __init__(
        self,
        repo: ResearchCollectionRepository,
        lithub_client: "LitHubClient | None" = None,
        lithub_enabled: bool = False,
    ) -> None:
        self.repo = repo
        self._lithub = lithub_client
        self._lithub_enabled = lithub_enabled and lithub_client is not None

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
    def _collect_paper_ids(collection: ResearchCollection) -> list[UUID]:
        """Gather LitHub paper_ids from a collection and its children's items."""
        ids: list[UUID] = []
        for it in (collection.items or []):
            if getattr(it, "paper_id", None) is not None:
                ids.append(it.paper_id)
        for child in (collection.children or []):
            for it in (child.items or []):
                if getattr(it, "paper_id", None) is not None:
                    ids.append(it.paper_id)
        return ids

    async def _fetch_lithub_papers(
        self, paper_ids: list[UUID],
    ) -> dict[str, dict[str, Any]]:
        """Best-effort bulk fetch of LitHub paper metadata keyed by paper_id str."""
        if not self._lithub_enabled or not paper_ids:
            return {}
        try:
            papers = await self._lithub.internal_papers_bulk(list(set(paper_ids)))
        except Exception as exc:  # noqa: BLE001 — enrichment is best-effort
            logger.warning("lithub_enrichment_fetch_failed", error=str(exc))
            return {}
        return {str(p["paper_id"]): p for p in papers if p.get("paper_id")}

    def _item_to_response(
        self,
        item: ResearchCollectionItem,
        lithub_papers: dict[str, dict[str, Any]] | None = None,
    ) -> CollectionItemResponse:
        raw_meta = item.metadata_extracted
        try:
            metadata = PaperMetadata.model_validate(raw_meta) if raw_meta else _DEFAULT_METADATA
        except Exception:
            metadata = _DEFAULT_METADATA
        # Backfill still-empty fields from the canonical LitHub paper (best-effort).
        if lithub_papers and getattr(item, "paper_id", None) is not None:
            paper = lithub_papers.get(str(item.paper_id))
            if paper:
                metadata = _merge_lithub_metadata(metadata, paper)
        return CollectionItemResponse(
            id=item.id,
            collection_id=item.collection_id,
            record_id=item.record_id,
            search_session_id=item.search_session_id,
            title=item.title,
            notes=item.notes,
            metadata=metadata,
            created_at=item.created_at,
        )

    @staticmethod
    def _to_response(
        collection: ResearchCollection,
        item_count: int = 0,
        children_count: int = 0,
        total_item_count: int = 0,
    ) -> CollectionResponse:
        return CollectionResponse(
            id=collection.id,
            name=collection.name,
            description=collection.description,
            parent_id=collection.parent_id,
            icon=collection.icon,
            color=collection.color,
            position=collection.position,
            item_count=item_count,
            children_count=children_count,
            total_item_count=total_item_count,
            created_at=collection.created_at,
            updated_at=collection.updated_at,
        )

    def _to_detail(
        self,
        collection: ResearchCollection,
        counts: dict[UUID, int],
        lithub_papers: dict[str, dict[str, Any]] | None = None,
    ) -> CollectionDetailResponse:
        own_items = [
            self._item_to_response(it, lithub_papers) for it in (collection.items or [])
        ]

        children_items: list[CollectionItemResponse] = []
        children_responses: list[CollectionResponse] = []
        for child in (collection.children or []):
            child_count = counts.get(child.id, 0)
            children_responses.append(self._to_response(
                child,
                item_count=child_count,
                children_count=len(child.children or []),
                total_item_count=child_count,
            ))
            children_items.extend(
                self._item_to_response(it, lithub_papers) for it in (child.items or [])
            )

        own_count = counts.get(collection.id, 0)
        total_count = own_count + sum(counts.get(c.id, 0) for c in (collection.children or []))

        return CollectionDetailResponse(
            id=collection.id,
            name=collection.name,
            description=collection.description,
            parent_id=collection.parent_id,
            icon=collection.icon,
            color=collection.color,
            position=collection.position,
            item_count=own_count,
            children_count=len(children_responses),
            total_item_count=total_count,
            created_at=collection.created_at,
            updated_at=collection.updated_at,
            items=own_items,
            all_items=own_items + children_items,
            children=children_responses,
        )

    # -- CRUD -----------------------------------------------------------------

    async def list_collections(self, user_id: UUID) -> CollectionTreeResponse:
        """Return tree of root collections with children nested."""
        roots = await self.repo.get_root_collections(user_id)
        counts = await self.repo.count_items_per_collection(user_id)
        all_paper_ids: list[UUID] = []
        for c in roots:
            all_paper_ids.extend(self._collect_paper_ids(c))
        lithub_papers = await self._fetch_lithub_papers(all_paper_ids)
        return CollectionTreeResponse(
            collections=[self._to_detail(c, counts, lithub_papers) for c in roots],
        )

    async def create_collection(
        self, user_id: UUID, payload: CreateCollectionRequest,
    ) -> CollectionResponse:
        if payload.parent_id is not None:
            parent = await self._get_owned_collection(payload.parent_id, user_id)
            parent_depth = await self.repo.get_nesting_depth(parent.id)
            if parent_depth + 1 >= _MAX_NESTING_DEPTH:
                raise CollectionNestingError()

        collection = await self.repo.create_collection(
            user_id=user_id,
            name=payload.name,
            description=payload.description,
            icon=payload.icon,
            color=payload.color,
            parent_id=payload.parent_id,
        )
        return self._to_response(collection, item_count=0)

    async def get_collection(
        self, collection_id: UUID, user_id: UUID,
    ) -> CollectionDetailResponse:
        collection = await self.repo.get_with_children(collection_id)
        if collection is None:
            raise CollectionNotFoundError(collection_id)
        if collection.user_id != user_id:
            raise CollectionAccessDeniedError()
        counts = await self.repo.count_items_per_collection(user_id)
        lithub_papers = await self._fetch_lithub_papers(
            self._collect_paper_ids(collection),
        )
        return self._to_detail(collection, counts, lithub_papers)

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
        own_count = counts.get(collection.id, 0)
        return self._to_response(collection, item_count=own_count)

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
            if item is not None:
                results.append(self._item_to_response(item))
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

        return self._item_to_response(new_item)
