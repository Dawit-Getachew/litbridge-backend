"""PICO recommender agent -- MeSH-backed reinforcement for inferred/weak elements."""

from __future__ import annotations

import asyncio
import re

import httpx
import structlog

from src.workflow.mesh_resolver import (
    resolve_mesh_descriptor,
    suggest_related_descriptors,
)
from src.workflow.state import PicoElement, WorkflowState

logger = structlog.get_logger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset({
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "and", "or",
    "is", "are", "was", "were", "with", "by", "from", "as", "be", "not",
    "no", "its", "it", "this", "that", "has", "have", "had",
})
_MAX_RELATED_PER_CONCEPT = 2

STANDARD_COMPARATORS = [
    "placebo",
    "standard of care",
    "usual care",
    "no intervention",
    "watchful waiting",
]


def _tokenize_meaningful(text: str) -> set[str]:
    """Tokenize and remove stopwords for relevance comparison."""
    return {
        tok for tok in _TOKEN_RE.findall(text.lower())
        if len(tok) > 1 and tok not in _STOPWORDS
    }


async def run_pico_recommendation(
    state: WorkflowState,
    client: httpx.AsyncClient,
    api_key: str,
    email: str,
) -> WorkflowState:
    """Reinforce weak or inferred PICO elements with MeSH-backed suggestions.

    For concepts where all elements are inferred or have low confidence,
    uses MeSH tree lookups from strong concepts to generate authoritative
    related terms. Also ensures C (Comparison) has standard comparators.
    """
    strong_tree_numbers: list[str] = []
    strong_concepts: list[str] = []

    for concept in ("P", "I", "C", "O"):
        elements = state.pico.get(concept, [])
        has_strong = any(
            not el.inferred and (el.confidence or 0) >= 0.5 for el in elements
        )
        if has_strong:
            strong_concepts.append(concept)

    for concept in strong_concepts:
        targets = state.atomic_targets.get(concept, [])
        for target in targets[:2]:
            try:
                descriptor, _ = await resolve_mesh_descriptor(
                    term=target, client=client, api_key=api_key, email=email,
                )
                if descriptor and descriptor.get("tree_numbers"):
                    strong_tree_numbers.extend(descriptor["tree_numbers"])
            except Exception:
                continue

    weak_concepts = [c for c in ("P", "I", "C", "O") if c not in strong_concepts]

    if not weak_concepts and not _needs_comparator_reinforcement(state):
        logger.info("pico_recommender_skip", reason="all concepts are strong")
        return state

    if strong_tree_numbers and weak_concepts:
        try:
            related = await suggest_related_descriptors(
                tree_numbers=strong_tree_numbers,
                client=client,
                api_key=api_key,
                email=email,
                max_siblings=5,
            )
            _apply_related_to_weak(state, weak_concepts, related)
        except Exception as exc:
            logger.warning("pico_recommender_related_error", error=str(exc))

    _ensure_standard_comparators(state)

    logger.info(
        "pico_recommender_complete",
        session_id=state.session_id,
        weak_concepts=weak_concepts,
        pico_counts={c: len(state.pico.get(c, [])) for c in "PICO"},
    )
    return state


def _needs_comparator_reinforcement(state: WorkflowState) -> bool:
    """Check if C elements are all inferred with no standard comparators."""
    c_elements = state.pico.get("C", [])
    if not c_elements:
        return True
    c_texts = {el.text.lower().strip() for el in c_elements}
    return not any(sc in c_texts for sc in STANDARD_COMPARATORS)


def _ensure_standard_comparators(state: WorkflowState) -> None:
    """Add standard comparators to C if missing."""
    existing_texts = {el.text.lower().strip() for el in state.pico.get("C", [])}
    existing_targets = set(state.atomic_targets.get("C", []))

    for comparator in STANDARD_COMPARATORS[:3]:
        if comparator.lower() not in existing_texts:
            state.pico.setdefault("C", []).append(PicoElement(
                text=comparator,
                confidence=0.6,
                inferred=True,
                provenance="mesh",
                facet="specific_agent",
            ))
            if comparator not in existing_targets:
                state.atomic_targets.setdefault("C", []).append(comparator)


def _apply_related_to_weak(
    state: WorkflowState,
    weak_concepts: list[str],
    related: list[dict],
) -> None:
    """Use related MeSH descriptors to suggest terms for weak PICO concepts.

    Only adds siblings that share at least one meaningful token with the
    original question, capped at _MAX_RELATED_PER_CONCEPT per concept.
    """
    concept_tree_prefixes = {
        "P": {"C", "F", "M"},
        "I": {"D", "E"},
        "C": {"D", "E"},
        "O": {"C", "E", "F", "G", "L", "N"},
    }

    question_tokens = _tokenize_meaningful(state.question)
    added_per_concept: dict[str, int] = {c: 0 for c in weak_concepts}

    for desc in related:
        name = str(desc.get("name", "")).strip()
        if not name:
            continue
        tree_nums = desc.get("tree_numbers", [])
        if not tree_nums:
            continue

        name_tokens = _tokenize_meaningful(name)
        if not (name_tokens & question_tokens):
            logger.debug(
                "pico_related_skipped_no_overlap",
                name=name,
                question_tokens=sorted(question_tokens)[:10],
            )
            continue

        top_categories = {tn.split(".")[0][0] for tn in tree_nums if tn}

        for concept in weak_concepts:
            if added_per_concept[concept] >= _MAX_RELATED_PER_CONCEPT:
                continue

            preferred_prefixes = concept_tree_prefixes.get(concept, set())
            if not top_categories & preferred_prefixes:
                continue

            existing_texts = {el.text.lower() for el in state.pico.get(concept, [])}
            if name.lower() in existing_texts:
                continue

            state.pico.setdefault(concept, []).append(PicoElement(
                text=name,
                confidence=0.4,
                inferred=True,
                provenance="mesh",
                facet=None,
            ))
            if name not in state.atomic_targets.get(concept, []):
                state.atomic_targets.setdefault(concept, []).append(name)
            added_per_concept[concept] += 1
            break
