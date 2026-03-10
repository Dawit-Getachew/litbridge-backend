"""PICO extraction agent -- extracts structured PICO from user input via LLM."""

from __future__ import annotations

import json
import re

import structlog

from src.ai.llm_client import LLMClient
from src.workflow.prompts import build_pico_extraction_messages
from src.workflow.state import PicoElement, WorkflowState

logger = structlog.get_logger(__name__)

_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", re.DOTALL)


def _strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences if present."""
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


async def run_pico_extraction(
    state: WorkflowState,
    llm: LLMClient,
) -> WorkflowState:
    """Extract PICO elements, atomic targets, and modifiers from the question.

    If the question already contains structured PICO (from user input),
    the LLM refines and normalizes it. If free text, the LLM extracts
    PICO components with confidence scores.
    """
    messages = build_pico_extraction_messages(state.question)

    payload = {
        "model": llm.model,
        "messages": messages,
        "temperature": 0.1,
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
                "pico_extraction_failed",
                status_code=response.status_code,
                body=response.text[:200],
            )
            state.errors.append({
                "stage": "pico_extraction",
                "error": f"LLM returned status {response.status_code}",
            })
            return state

        content = llm._extract_message_content(response.json())
        if not content:
            state.errors.append({
                "stage": "pico_extraction",
                "error": "Empty LLM response",
            })
            return state

        data = json.loads(_strip_markdown_fences(content))
    except json.JSONDecodeError as exc:
        logger.warning("pico_extraction_json_error", error=str(exc))
        state.errors.append({
            "stage": "pico_extraction",
            "error": f"Invalid JSON from LLM: {exc}",
        })
        return state
    except Exception as exc:
        logger.warning("pico_extraction_error", error=str(exc))
        state.errors.append({
            "stage": "pico_extraction",
            "error": str(exc),
        })
        return state

    pico_raw = data.get("pico", {})
    for concept in ("P", "I", "C", "O"):
        elements = pico_raw.get(concept, [])
        state.pico[concept] = [
            PicoElement(
                text=str(el.get("text", "")).strip(),
                confidence=_safe_float(el.get("confidence")),
                provenance="llm",
                facet=str(el["facet"]) if el.get("facet") is not None else None,
            )
            for el in elements
            if isinstance(el, dict) and str(el.get("text", "")).strip()
        ]

    targets_raw = data.get("atomic_targets", {})
    for concept in ("P", "I", "C", "O"):
        raw = targets_raw.get(concept, [])
        state.atomic_targets[concept] = [
            str(t).strip() for t in raw if isinstance(t, str) and str(t).strip()
        ]
        if not state.atomic_targets[concept] and state.pico[concept]:
            state.atomic_targets[concept] = [
                el.text for el in state.pico[concept]
            ]

    modifiers_raw = data.get("modifiers", {})
    for concept in ("P", "I", "C", "O"):
        concept_mods = modifiers_raw.get(concept, {})
        if isinstance(concept_mods, dict):
            state.modifiers[concept] = {
                k: [str(v).strip() for v in vals if str(v).strip()]
                for k, vals in concept_mods.items()
                if isinstance(vals, list)
            }

    logger.info(
        "pico_extraction_complete",
        session_id=state.session_id,
        pico_counts={c: len(state.pico[c]) for c in "PICO"},
        target_counts={c: len(state.atomic_targets[c]) for c in "PICO"},
    )

    return state
