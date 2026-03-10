"""Multi-database query adapter -- adapts the structured query for each database."""

from __future__ import annotations

import re

from src.workflow.state import ConceptBlock, StructuredQuery


def _strip_field_tags(clause: str) -> str:
    """Remove PubMed field tags like [tiab], [MeSH Terms], [MeSH Terms:noexp]."""
    cleaned = re.sub(r"\[[\w\s:]+\]", "", clause)
    return cleaned.strip().strip('"').strip()


def _extract_terms_from_block(block: ConceptBlock) -> list[str]:
    """Extract clean terms from a concept block."""
    terms: list[str] = []
    seen: set[str] = set()

    for clause in block.mesh_clauses + block.text_clauses:
        term = _strip_field_tags(clause)
        if not term:
            continue
        if "/" in term:
            term = term.split("/")[0].strip()
        key = term.lower()
        if key not in seen:
            seen.add(key)
            terms.append(term)

    return terms


def adapt_for_pubmed(query: StructuredQuery) -> str:
    """PubMed: use the native Boolean as-is."""
    return query.pubmed_query


def adapt_for_europepmc(query: StructuredQuery) -> str:
    """Europe PMC: Boolean with TITLE/ABSTRACT fields instead of [tiab].

    Europe PMC supports Boolean operators (AND, OR, NOT),
    parentheses, and quoted phrases. Uses (TITLE:"term" OR ABSTRACT:"term")
    instead of [tiab]. MeSH synonym search is enabled by default.
    """
    concept_strings: list[str] = []

    for block in query.concept_blocks:
        clauses: list[str] = []
        seen: set[str] = set()

        for mc in block.mesh_clauses:
            term = _strip_field_tags(mc)
            if not term or term.lower() in seen:
                continue
            seen.add(term.lower())
            if "/" in term:
                term = term.split("/")[0].strip()
            quoted = f'"{term}"' if " " in term else term
            clauses.append(quoted)

        for tc in block.text_clauses:
            term = _strip_field_tags(tc)
            if not term or term.lower() in seen:
                continue
            seen.add(term.lower())
            quoted = f'"{term}"' if " " in term else term
            clauses.append(f'(TITLE:{quoted} OR ABSTRACT:{quoted})')

        if not clauses:
            continue

        concept_or = " OR ".join(clauses)

        if block.modifier_clauses:
            mod_terms = [_strip_field_tags(m) for m in block.modifier_clauses]
            mod_terms = [m for m in mod_terms if m]
            if mod_terms:
                mod_or = " OR ".join(
                    f'"{m}"' if " " in m else m for m in mod_terms
                )
                concept_strings.append(f"(({concept_or}) AND ({mod_or}))")
                continue

        concept_strings.append(f"({concept_or})")

    return " AND ".join(concept_strings)


def adapt_for_openalex(query: StructuredQuery) -> str:
    """OpenAlex: clean term-based Boolean for the search= parameter.

    OpenAlex supports AND, OR, NOT operators with parentheses.
    No field tags. Terms are searched across title, abstract, and concepts.
    """
    concept_strings: list[str] = []

    for block in query.concept_blocks:
        terms = _extract_terms_from_block(block)
        if not terms:
            continue

        quoted = [f'"{t}"' if " " in t else t for t in terms]
        concept_or = " OR ".join(quoted)

        if block.modifier_clauses:
            mod_terms = [_strip_field_tags(m) for m in block.modifier_clauses]
            mod_terms = [m for m in mod_terms if m]
            if mod_terms:
                mod_or = " OR ".join(
                    f'"{m}"' if " " in m else m for m in mod_terms
                )
                concept_strings.append(f"(({concept_or}) AND ({mod_or}))")
                continue

        concept_strings.append(f"({concept_or})")

    return " AND ".join(concept_strings)


def adapt_for_clinicaltrials(query: StructuredQuery) -> str:
    """ClinicalTrials.gov V2: flatten PICO terms into a single query.term string.

    The existing ClinicalTrialsRepository.search() passes the query string
    directly to the query.term API parameter. We combine all concept terms
    using AND between concepts and OR within concepts.
    """
    concept_parts: list[str] = []

    for block in query.concept_blocks:
        terms = _extract_terms_from_block(block)
        if not terms:
            continue

        quoted = [f'"{t}"' if " " in t else t for t in terms[:5]]
        concept_or = " OR ".join(quoted)
        concept_parts.append(f"({concept_or})")

    return " AND ".join(concept_parts)


def adapt_all(query: StructuredQuery) -> dict[str, str]:
    """Generate adapted queries for all databases.

    Returns a dict mapping source name to adapted query string.
    """
    return {
        "pubmed": adapt_for_pubmed(query),
        "europepmc": adapt_for_europepmc(query),
        "openalex": adapt_for_openalex(query),
        "clinicaltrials": adapt_for_clinicaltrials(query),
    }
