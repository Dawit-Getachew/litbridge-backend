"""Workflow state models for the structured search HITL lifecycle."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

Concept = Literal["P", "I", "C", "O"]
AwaitingStage = Literal["keywords_review", "mesh_review", "query_review"]


def _empty_concept_dict() -> dict[str, Any]:
    return {"P": {}, "I": {}, "C": {}, "O": {}}


def _empty_concept_list() -> dict[str, list]:
    return {"P": [], "I": [], "C": [], "O": []}


class PicoElement(BaseModel):
    """One extracted PICO element (a single base term)."""

    text: str
    confidence: float | None = None
    inferred: bool = False
    provenance: Literal["llm", "user"] = "llm"
    facet: str | None = None


class Suggestion(BaseModel):
    """A keyword synonym candidate for a (concept, base_term)."""

    term: str
    concept: Concept
    base_term: str
    status: Literal["suggested", "accepted", "rejected"] = "suggested"
    variant: Literal[
        "synonym", "abbreviation", "spelling", "lay_term", "phrase_variant"
    ] | None = None
    confidence: float | None = None


class MeshQualifiers(BaseModel):
    """Allowed and selected MeSH subheadings for a descriptor."""

    allowed: list[str] = Field(default_factory=list)
    selected: list[str] = Field(default_factory=list)


class MeshDescriptor(BaseModel):
    """A resolved MeSH descriptor for a (concept, base_term)."""

    mesh_term: str
    concept: Concept
    base_term: str
    status: Literal["suggested", "accepted", "rejected"] = "suggested"

    descriptor_uid: str | None = None
    descriptor_name: str | None = None
    tree_numbers: list[str] = Field(default_factory=list)
    min_depth: int | None = None

    entry_terms: list[str] = Field(default_factory=list)
    entry_terms_selected: list[str] = Field(default_factory=list)

    qualifiers: MeshQualifiers = Field(default_factory=MeshQualifiers)
    explode: bool = True
    scope_note: str | None = None


class ConceptBlock(BaseModel):
    """Intermediate representation of one PICO concept's query logic."""

    concept: Concept
    mesh_clauses: list[str] = Field(default_factory=list)
    text_clauses: list[str] = Field(default_factory=list)
    modifier_clauses: list[str] = Field(default_factory=list)


class StructuredQuery(BaseModel):
    """Final Boolean query with per-database adaptations."""

    pubmed_query: str = ""
    adapted_queries: dict[str, str] = Field(default_factory=dict)
    concept_blocks: list[ConceptBlock] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class WorkflowState(BaseModel):
    """Full session state for the structured search workflow."""

    session_id: str
    user_id: str | None = None
    question: str
    original_query_type: str = "free"

    pico: dict[str, list[PicoElement]] = Field(default_factory=_empty_concept_list)
    atomic_targets: dict[str, list[str]] = Field(default_factory=_empty_concept_list)
    modifiers: dict[str, dict[str, list[str]]] = Field(default_factory=_empty_concept_dict)

    synonyms: dict[str, dict[str, list[Suggestion]]] = Field(
        default_factory=_empty_concept_dict
    )
    mesh: dict[str, dict[str, list[MeshDescriptor]]] = Field(
        default_factory=_empty_concept_dict
    )

    structured_query: StructuredQuery | None = None
    awaiting: AwaitingStage | None = None

    selected_sources: list[str] = Field(default_factory=list)
    search_mode: str = "quick"
    search_id: str | None = None

    errors: list[dict[str, str]] = Field(default_factory=list)
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
