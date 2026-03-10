"""MeSH resolution agent -- resolves MeSH descriptors for all atomic targets concurrently."""

from __future__ import annotations

import asyncio

import httpx
import structlog

from src.workflow.mesh_resolver import (
    descriptor_to_mesh_suggestion,
    resolve_mesh_descriptor,
)
from src.workflow.state import WorkflowState

logger = structlog.get_logger(__name__)


async def run_mesh_resolution(
    state: WorkflowState,
    client: httpx.AsyncClient,
    api_key: str,
    email: str,
) -> WorkflowState:
    """Resolve MeSH descriptors for all atomic targets across P/I/C/O.

    Runs up to 10 concurrent E-utilities calls. Populates state.mesh
    and sets awaiting to mesh_review.
    """
    tasks: list[asyncio.Task] = []
    task_keys: list[tuple[str, str]] = []

    for concept in ("P", "I", "C", "O"):
        targets = state.atomic_targets.get(concept, [])
        for base_term in targets:
            task = asyncio.create_task(
                resolve_mesh_descriptor(
                    term=base_term,
                    client=client,
                    api_key=api_key,
                    email=email,
                )
            )
            tasks.append(task)
            task_keys.append((concept, base_term))

    if not tasks:
        state.awaiting = "mesh_review"
        return state

    results = await asyncio.gather(*tasks, return_exceptions=True)

    resolved_count = 0
    unresolved_count = 0

    for (concept, base_term), result in zip(task_keys, results):
        if concept not in state.mesh:
            state.mesh[concept] = {}
        if base_term not in state.mesh[concept]:
            state.mesh[concept][base_term] = []

        if isinstance(result, Exception):
            logger.warning(
                "mesh_resolution_error",
                concept=concept,
                base_term=base_term,
                error=str(result),
            )
            state.errors.append({
                "stage": "mesh_resolution",
                "error": f"Failed to resolve '{base_term}' ({concept}): {result}",
            })
            unresolved_count += 1
            continue

        descriptor, _translation = result
        if descriptor is None:
            logger.info(
                "mesh_unresolved",
                concept=concept,
                base_term=base_term,
            )
            unresolved_count += 1
            continue

        suggestion = descriptor_to_mesh_suggestion(
            descriptor,
            concept=concept,
            base_term=base_term,
        )
        state.mesh[concept][base_term] = [suggestion]
        resolved_count += 1

    state.awaiting = "mesh_review"

    logger.info(
        "mesh_resolution_complete",
        session_id=state.session_id,
        resolved=resolved_count,
        unresolved=unresolved_count,
    )

    return state
