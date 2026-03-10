"""Boolean query builder -- constructs PubMed Boolean from structured IR."""

from __future__ import annotations

from src.workflow.state import (
    ConceptBlock,
    MeshDescriptor,
    StructuredQuery,
    Suggestion,
    WorkflowState,
)


def _quote(term: str) -> str:
    """Quote a term if it contains spaces."""
    return f'"{term}"' if " " in term else term


def _build_mesh_clause(desc: MeshDescriptor) -> list[str]:
    """Build MeSH clause(s) for one accepted descriptor."""
    clauses: list[str] = []

    if desc.descriptor_uid and desc.mesh_term:
        tag = "[MeSH Terms]" if desc.explode else "[MeSH Terms:noexp]"

        selected_quals = desc.qualifiers.selected if desc.qualifiers else []
        if selected_quals:
            for qual in selected_quals:
                clauses.append(f'{_quote(f"{desc.mesh_term}/{qual}")}{tag}')
        else:
            clauses.append(f"{_quote(desc.mesh_term)}{tag}")

    for entry in desc.entry_terms_selected:
        entry = entry.strip()
        if entry:
            clauses.append(f"{_quote(entry)}[tiab]")

    return clauses


def _build_text_clauses(
    base_term: str,
    synonyms: list[Suggestion],
) -> list[str]:
    """Build [tiab] clauses from base term + accepted synonyms."""
    clauses = [f"{_quote(base_term)}[tiab]"]

    for syn in synonyms:
        if syn.status == "accepted" and syn.term.strip():
            clauses.append(f"{_quote(syn.term.strip())}[tiab]")

    return clauses


def _build_modifier_clause(
    modifiers: dict[str, list[str]],
) -> list[str]:
    """Build modifier tokens as [tiab] clauses."""
    clauses: list[str] = []
    for _category, terms in modifiers.items():
        for term in terms:
            term = term.strip()
            if term:
                clauses.append(f"{_quote(term)}[tiab]")
    return clauses


def build_structured_query(state: WorkflowState) -> StructuredQuery:
    """Build the PubMed Boolean query from the workflow state.

    Each PICO concept (P, I, C, O) becomes one block:
      (mesh_clause_1 OR mesh_clause_2 OR text_clause_1 OR text_clause_2)

    Concept blocks are ANDed together. Modifiers are appended to their
    concept block as: (concept_block AND (modifier_1 OR modifier_2)).
    """
    concept_blocks: list[ConceptBlock] = []
    concept_strings: list[str] = []
    warnings: list[str] = []

    for concept in ("P", "I", "C", "O"):
        mesh_groups = state.mesh.get(concept, {})
        synonym_groups = state.synonyms.get(concept, {})
        concept_modifiers = state.modifiers.get(concept, {})
        targets = state.atomic_targets.get(concept, [])

        all_mesh_clauses: list[str] = []
        all_text_clauses: list[str] = []

        for base_term in targets:
            if not base_term or not base_term.strip():
                continue
            descriptors = mesh_groups.get(base_term, [])
            accepted_descs = [d for d in descriptors if d.status != "rejected"]

            for desc in accepted_descs:
                all_mesh_clauses.extend(_build_mesh_clause(desc))

            synonyms = synonym_groups.get(base_term, [])
            all_text_clauses.extend(_build_text_clauses(base_term, synonyms))

        or_parts = all_mesh_clauses + all_text_clauses
        if not or_parts:
            continue

        unique_parts = list(dict.fromkeys(or_parts))
        concept_or = " OR ".join(unique_parts)

        modifier_clauses = _build_modifier_clause(concept_modifiers)

        block = ConceptBlock(
            concept=concept,
            mesh_clauses=all_mesh_clauses,
            text_clauses=all_text_clauses,
            modifier_clauses=modifier_clauses,
        )
        concept_blocks.append(block)

        if modifier_clauses:
            mod_or = " OR ".join(modifier_clauses)
            concept_str = f"(({concept_or}) AND ({mod_or}))"
        else:
            concept_str = f"({concept_or})"

        concept_strings.append(concept_str)

    pubmed_query = " AND ".join(concept_strings)

    if not concept_strings:
        warnings.append("No PICO concepts produced any search terms.")

    return StructuredQuery(
        pubmed_query=pubmed_query,
        concept_blocks=concept_blocks,
        warnings=warnings,
    )
