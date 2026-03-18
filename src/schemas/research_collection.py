"""Request / response DTOs for the Research Collections feature."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


# -- Paper Metadata (AI-extracted, used in table view) ------------------------

class PaperMetadata(BaseModel):
    """Structured metadata extracted from a paper for table view display."""

    study_details: str = "Not reported"
    study_design: str = "Not reported"
    setting: str = "Not reported"
    interventions: str = "Not reported"
    sample_size: str = "Not reported"
    primary_outcome: str = "Not reported"
    secondary_outcome: str = "Not reported"
    primary_statistics: str = "Not reported"
    secondary_statistics: str = "Not reported"
    key_findings: str = "Not reported"


# -- Requests -----------------------------------------------------------------

class CreateCollectionRequest(BaseModel):
    """Body for POST /collections."""

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    icon: str | None = Field(default=None, max_length=64)
    color: str | None = Field(default=None, max_length=32)
    parent_id: UUID | None = None


class UpdateCollectionRequest(BaseModel):
    """Body for PATCH /collections/{id}."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    icon: str | None = Field(default=None, max_length=64)
    color: str | None = Field(default=None, max_length=32)
    position: int | None = None


class AddRecordItem(BaseModel):
    """Single record to add to a collection."""

    record_id: str = Field(..., min_length=1)
    search_session_id: UUID
    title: str | None = Field(default=None, max_length=512)
    notes: str | None = None


class AddRecordsRequest(BaseModel):
    """Body for POST /collections/{id}/records."""

    records: list[AddRecordItem] = Field(..., min_length=1)


class MoveRecordRequest(BaseModel):
    """Body for POST /collections/{id}/records/{record_id}/move."""

    target_collection_id: UUID


# -- Responses ----------------------------------------------------------------

class CollectionItemResponse(BaseModel):
    """A single record within a collection, always includes metadata."""

    id: UUID
    collection_id: UUID
    record_id: str
    search_session_id: UUID
    title: str | None
    notes: str | None
    metadata: PaperMetadata
    created_at: datetime

    model_config = {"from_attributes": True}


class CollectionResponse(BaseModel):
    """Collection summary (no items inlined)."""

    id: UUID
    name: str
    description: str | None
    parent_id: UUID | None
    icon: str | None
    color: str | None
    position: int
    item_count: int = 0
    children_count: int = 0
    total_item_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CollectionDetailResponse(CollectionResponse):
    """Collection with its own items, all descendant items aggregated, and children."""

    items: list[CollectionItemResponse] = []
    all_items: list[CollectionItemResponse] = []
    children: list[CollectionResponse] = []


class CollectionTreeResponse(BaseModel):
    """Full tree of root collections with children nested."""

    collections: list[CollectionDetailResponse]
