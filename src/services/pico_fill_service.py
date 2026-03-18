"""PICO fill service -- ensures all four PICO elements are populated."""

from __future__ import annotations

import json
import re

import structlog

from src.ai.llm_client import LLMClient
from src.schemas.pico import PICOInput
from src.workflow.prompts import build_pico_fill_messages
from src.workflow.state import PicoElement, WorkflowState

logger = structlog.get_logger(__name__)

_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", re.DOTALL)

_CONCEPT_TO_FIELD = {"P": "population", "I": "intervention", "C": "comparison", "O": "outcome"}


def _strip_fences(text: str) -> str:
    text = text.strip()
    m = _FENCE_RE.match(text)
    return m.group(1).strip() if m else text


async def fill_missing_pico(pico: PICOInput, llm: LLMClient) -> PICOInput:
    """Fill empty PICO elements via a single LLM call.

    Used by the direct search path (POST /search, /preview, /stream).
    Returns a new PICOInput with all four elements populated and
    *_inferred flags set for AI-filled slots.
    """
    missing = [
        key for key, val in {
            "P": pico.population,
            "I": pico.intervention,
            "C": pico.comparison,
            "O": pico.outcome,
        }.items()
        if not val or not val.strip()
    ]

    if not missing:
        return pico

    messages = build_pico_fill_messages(
        population=pico.population,
        intervention=pico.intervention,
        comparison=pico.comparison,
        outcome=pico.outcome,
    )

    payload = {
        "model": llm.model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 800,
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
            logger.warning("pico_fill_failed", status=response.status_code)
            return pico

        content = llm._extract_message_content(response.json())
        if not content:
            return pico

        data = json.loads(_strip_fences(content))
        if not isinstance(data, dict):
            logger.warning("pico_fill_unexpected_type", data_type=type(data).__name__)
            return pico
    except Exception as exc:
        logger.warning("pico_fill_error", error=str(exc))
        return pico

    filled = pico.model_copy()

    for concept, field in _CONCEPT_TO_FIELD.items():
        if concept not in missing:
            continue

        suggestions = data.get(field, [])
        if isinstance(suggestions, list) and suggestions:
            joined = "; ".join(str(s).strip() for s in suggestions if str(s).strip())
            if joined:
                setattr(filled, field, joined)
                setattr(filled, f"{field}_inferred", True)

    logger.info(
        "pico_fill_complete",
        missing=missing,
        filled=[f for f in missing if getattr(filled, _CONCEPT_TO_FIELD[f])],
    )
    return filled


async def fill_missing_pico_state(
    state: WorkflowState,
    llm: LLMClient,
) -> WorkflowState:
    """Ensure all four PICO concepts are populated in workflow state.

    Runs after pico_agent extraction. For any concept with zero elements,
    calls the LLM to infer plausible candidates and adds them as
    PicoElement(inferred=True).
    """
    empty_concepts = [c for c in ("P", "I", "C", "O") if not state.pico.get(c)]
    if not empty_concepts:
        return state

    provided: dict[str, str | None] = {}
    for concept in ("P", "I", "C", "O"):
        elements = state.pico.get(concept, [])
        if elements:
            provided[_CONCEPT_TO_FIELD[concept]] = "; ".join(el.text for el in elements)
        else:
            provided[_CONCEPT_TO_FIELD[concept]] = None

    messages = build_pico_fill_messages(
        population=provided["population"],
        intervention=provided["intervention"],
        comparison=provided["comparison"],
        outcome=provided["outcome"],
    )

    payload = {
        "model": llm.model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 800,
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
            logger.warning("pico_fill_state_failed", status=response.status_code)
            return state

        content = llm._extract_message_content(response.json())
        if not content:
            return state

        data = json.loads(_strip_fences(content))
        if not isinstance(data, dict):
            logger.warning("pico_fill_state_unexpected_type", data_type=type(data).__name__)
            return state
    except Exception as exc:
        logger.warning("pico_fill_state_error", error=str(exc))
        return state

    for concept in empty_concepts:
        field = _CONCEPT_TO_FIELD[concept]
        suggestions = data.get(field, [])
        if not isinstance(suggestions, list):
            continue

        new_elements: list[PicoElement] = []
        new_targets: list[str] = []
        for term in suggestions:
            text = str(term).strip()
            if not text:
                continue
            new_elements.append(PicoElement(
                text=text,
                confidence=0.5,
                inferred=True,
                provenance="llm",
            ))
            new_targets.append(text)

        if new_elements:
            state.pico[concept] = new_elements
            state.atomic_targets[concept] = new_targets

    logger.info(
        "pico_fill_state_complete",
        empty_before=empty_concepts,
        filled_after={c: len(state.pico.get(c, [])) for c in empty_concepts},
    )
    return state
