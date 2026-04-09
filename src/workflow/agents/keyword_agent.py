"""Keyword expansion agent -- generates synonyms for each PICO concept via LLM."""

from __future__ import annotations

import json
import re

import structlog

from src.ai.llm_client import LLMClient
from src.workflow.prompts import build_keyword_expansion_messages
from src.workflow.state import Suggestion, WorkflowState

logger = structlog.get_logger(__name__)

_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", re.DOTALL)
_TOKEN_RE = re.compile(r"[a-z0-9]+")

_VALID_VARIANTS = frozenset({
    "synonym", "abbreviation", "spelling", "lay_term", "phrase_variant",
})

_MIN_CONFIDENCE = 0.4
_MAX_TERM_LENGTH = 80


def _strip_markdown_fences(text: str) -> str:
    text = text.strip()
    m = _FENCE_RE.match(text)
    return m.group(1).strip() if m else text


def _safe_float(val: object) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _safe_variant(val: object) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    return s if s in _VALID_VARIANTS else "synonym"


def _tokenize(text: str) -> set[str]:
    return {tok for tok in _TOKEN_RE.findall(text.lower()) if len(tok) > 1}


def _collect_other_targets(
    state: WorkflowState,
    current_concept: str,
) -> set[str]:
    """Gather lowercased base terms from all PICO concepts except the current one."""
    others: set[str] = set()
    for concept in ("P", "I", "C", "O"):
        if concept == current_concept:
            continue
        for target in state.atomic_targets.get(concept, []):
            others.add(target.strip().lower())
    return others


def _is_relevant(
    term: str,
    base_term: str,
    confidence: float | None,
    other_targets: set[str],
) -> bool:
    """Reject suggestions with no lexical tie to the base term or cross-concept bleed."""
    if len(term) > _MAX_TERM_LENGTH:
        return False

    if term.lower() in other_targets:
        return False

    if confidence is not None and confidence < _MIN_CONFIDENCE:
        return False

    base_tokens = _tokenize(base_term)
    term_tokens = _tokenize(term)
    has_overlap = bool(base_tokens & term_tokens)

    if not has_overlap and (confidence is None or confidence < 0.5):
        return False

    return True


def _deduplicate(suggestions: list[Suggestion]) -> list[Suggestion]:
    """Drop case-insensitive duplicates, keeping first occurrence."""
    seen: set[str] = set()
    unique: list[Suggestion] = []
    for s in suggestions:
        key = s.term.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(s)
    return unique


async def run_keyword_expansion(
    state: WorkflowState,
    llm: LLMClient,
) -> WorkflowState:
    """Expand keywords for all PICO concepts and set awaiting to keywords_review.

    One LLM call per concept (batching all atomic targets for that concept).
    Results are post-filtered for relevance, confidence, length, and dedup.
    """
    for concept in ("P", "I", "C", "O"):
        targets = state.atomic_targets.get(concept, [])
        if not targets:
            continue

        pico_context = {
            c: state.atomic_targets.get(c, [])
            for c in ("P", "I", "C", "O")
        }
        messages = build_keyword_expansion_messages(
            concept, targets, pico_context=pico_context,
        )

        payload = {
            "model": llm.model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 1500,
            "response_format": {"type": "json_object"},
        }

        try:
            response = await llm.client.post(
                f"{llm.base_url}/chat/completions",
                json=payload,
                headers=llm._headers(),
                timeout=30.0,
            )

            if response.status_code >= 400:
                logger.warning(
                    "keyword_expansion_failed",
                    concept=concept,
                    status_code=response.status_code,
                )
                state.errors.append({
                    "stage": "keyword_expansion",
                    "error": f"LLM status {response.status_code} for concept {concept}",
                })
                continue

            content = llm._extract_message_content(response.json())
            if not content:
                state.errors.append({
                    "stage": "keyword_expansion",
                    "error": f"Empty LLM response for concept {concept}",
                })
                continue

            data = json.loads(_strip_markdown_fences(content))
        except json.JSONDecodeError as exc:
            logger.warning(
                "keyword_expansion_json_error",
                concept=concept,
                error=str(exc),
            )
            state.errors.append({
                "stage": "keyword_expansion",
                "error": f"Invalid JSON for concept {concept}: {exc}",
            })
            continue
        except Exception as exc:
            logger.warning(
                "keyword_expansion_error",
                concept=concept,
                error=str(exc),
            )
            state.errors.append({
                "stage": "keyword_expansion",
                "error": f"Error expanding concept {concept}: {exc}",
            })
            continue

        keywords_raw = data.get("keywords", {})
        if concept not in state.synonyms:
            state.synonyms[concept] = {}

        other_targets = _collect_other_targets(state, concept)

        for base_term, suggestions in keywords_raw.items():
            base_term = str(base_term).strip()
            if not base_term or not isinstance(suggestions, list):
                continue

            raw_suggestions = [
                Suggestion(
                    term=str(s.get("term", "")).strip(),
                    concept=concept,
                    base_term=base_term,
                    status="suggested",
                    variant=_safe_variant(s.get("variant")),
                    confidence=_safe_float(s.get("confidence")),
                )
                for s in suggestions
                if isinstance(s, dict) and str(s.get("term", "")).strip()
            ]

            filtered = [
                s for s in raw_suggestions
                if _is_relevant(s.term, base_term, s.confidence, other_targets)
            ]

            rejected_count = len(raw_suggestions) - len(filtered)
            if rejected_count:
                logger.debug(
                    "keyword_suggestions_filtered",
                    concept=concept,
                    base_term=base_term,
                    rejected=rejected_count,
                    kept=len(filtered),
                )

            state.synonyms[concept][base_term] = _deduplicate(filtered)

    state.awaiting = "keywords_review"

    logger.info(
        "keyword_expansion_complete",
        session_id=state.session_id,
        synonym_counts={
            c: sum(len(v) for v in groups.values())
            for c, groups in state.synonyms.items()
        },
    )

    return state
