"""Request/response DTOs for the structured search workflow API."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── Requests ──────────────────────────────────────────────────────


class WorkflowStartRequest(BaseModel):
    """Start a new workflow session."""

    query: str = ""
    query_type: str = "free"
    pico: dict[str, str] | None = None
    workflow: bool = True


class PicoPreviewRequest(BaseModel):
    """Quick PICO extraction preview (stateless)."""

    question: str = Field(..., min_length=5)


class PicoEditAction(str, Enum):
    add = "add"
    remove = "remove"
    edit = "edit"


class PicoEdit(BaseModel):
    concept: str
    action: PicoEditAction
    text: str | None = None
    index: int | None = None


class KeywordAction(str, Enum):
    accept = "accept"
    reject = "reject"
    edit = "edit"
    add = "add"


class KeywordDecision(BaseModel):
    action: KeywordAction
    term: str | None = None
    new_term: str | None = None


class KeywordFeedbackItem(BaseModel):
    concept: str
    base_term: str
    decisions: list[KeywordDecision] = Field(default_factory=list)


class KeywordFeedbackRequest(BaseModel):
    """Combined PICO edits + keyword decisions."""

    pico_edits: list[PicoEdit] = Field(default_factory=list)
    keyword_decisions: list[KeywordFeedbackItem] = Field(default_factory=list)


class MeshResolveRequest(BaseModel):
    """Manually resolve a MeSH term."""

    concept: str
    base_term: str
    mesh_term: str


class MeshFeedbackAction(str, Enum):
    accept = "accept"
    reject = "reject"
    none = "none"


class MeshFeedbackItem(BaseModel):
    concept: str
    base_term: str
    mesh_term: str
    action: MeshFeedbackAction = MeshFeedbackAction.none
    entry_terms_selected: list[str] = Field(default_factory=list)
    qualifiers_selected: list[str] = Field(default_factory=list)
    explode: bool = True


class MeshFeedbackRequest(BaseModel):
    items: list[MeshFeedbackItem]


class QueryEditRequest(BaseModel):
    """Manually edit the Boolean query."""

    pubmed_query: str


class WorkflowSearchRequest(BaseModel):
    """Execute the approved search."""

    search_mode: str = "quick"
    sources: list[str] | None = None
    max_results: int = Field(default=100, ge=1, le=5000)


# ── Responses ─────────────────────────────────────────────────────


class WorkflowStartResponse(BaseModel):
    workflow_session_id: str
    awaiting: str | None
    pico: dict[str, list[dict[str, Any]]]
    keywords: dict[str, dict[str, list[dict[str, Any]]]]
    errors: list[dict[str, str]] = Field(default_factory=list)


class PicoPreviewResponse(BaseModel):
    pico: dict[str, list[dict[str, Any]]]


class KeywordFeedbackResponse(BaseModel):
    workflow_session_id: str
    awaiting: str | None
    mesh: dict[str, dict[str, list[dict[str, Any]]]]
    errors: list[dict[str, str]] = Field(default_factory=list)


class MeshResolveResponse(BaseModel):
    found: bool
    suggestion: dict[str, Any] | None = None
    message: str | None = None


class MeshFeedbackResponse(BaseModel):
    workflow_session_id: str
    awaiting: str | None
    query: dict[str, Any] | None = None
    errors: list[dict[str, str]] = Field(default_factory=list)


class QueryPreviewResponse(BaseModel):
    pubmed_query: str
    adapted_queries: dict[str, str]
    warnings: list[str] = Field(default_factory=list)


class WorkflowSearchResponse(BaseModel):
    search_id: str
    workflow_session_id: str
    total_identified: int
    total_after_dedup: int
    sources_completed: list[str]
    sources_failed: list[str]
