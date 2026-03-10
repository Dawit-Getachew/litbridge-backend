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

_VALID_VARIANTS = frozenset({
    "synonym", "abbreviation", "spelling", "lay_term", "phrase_variant",
})


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


async def run_keyword_expansion(
    state: WorkflowState,
    llm: LLMClient,
) -> WorkflowState:
    """Expand keywords for all PICO concepts and set awaiting to keywords_review.

    One LLM call per concept (batching all atomic targets for that concept).
    """
    for concept in ("P", "I", "C", "O"):
        targets = state.atomic_targets.get(concept, [])
        if not targets:
            continue

        messages = build_keyword_expansion_messages(concept, targets)

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

        for base_term, suggestions in keywords_raw.items():
            base_term = str(base_term).strip()
            if not base_term or not isinstance(suggestions, list):
                continue

            state.synonyms[concept][base_term] = [
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
