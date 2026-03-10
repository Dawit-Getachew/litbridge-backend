"""Request / response DTOs for the Library (collections) feature."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


# ── Requests ──────────────────────────────────────────────────────

class CreateLibraryRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    parent_id: UUID | None = None
    icon: str | None = Field(default=None, max_length=64)
    color: str | None = Field(default=None, max_length=32)


class UpdateLibraryRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    parent_id: UUID | None = None
    icon: str | None = Field(default=None, max_length=64)
    color: str | None = Field(default=None, max_length=32)
    position: int | None = None


class AddItemsRequest(BaseModel):
    search_session_ids: list[UUID] = Field(..., min_length=1)
    notes: str | None = None


# ── Responses ─────────────────────────────────────────────────────

class LibraryItemResponse(BaseModel):
    id: UUID
    library_id: UUID
    search_session_id: UUID
    notes: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class LibraryResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    parent_id: UUID | None
    icon: str | None
    color: str | None
    position: int
    item_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LibraryDetailResponse(LibraryResponse):
    """Library with its items and children inlined."""
    items: list[LibraryItemResponse] = []
    children: list[LibraryResponse] = []


class LibraryTreeResponse(BaseModel):
    """Top-level list returned from GET /library."""
    libraries: list[LibraryDetailResponse]


class SearchSessionBrief(BaseModel):
    """Minimal info about a search session for listing."""
    id: UUID
    query: str
    query_type: str
    search_mode: str
    sources: list[str]
    status: str
    total_after_dedup: int
    created_at: datetime

    model_config = {"from_attributes": True}


class UserSearchesResponse(BaseModel):
    searches: list[SearchSessionBrief]
    total: int
