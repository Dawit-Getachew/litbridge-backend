"""Redis-backed workflow session persistence."""

from __future__ import annotations

import json

import structlog
from redis.asyncio import Redis

from src.workflow.state import WorkflowState

logger = structlog.get_logger(__name__)

WORKFLOW_KEY_PREFIX = "litbridge:workflow:"
DEFAULT_TTL_SECONDS = 86400  # 24 hours


def _key(session_id: str) -> str:
    return f"{WORKFLOW_KEY_PREFIX}{session_id}"


async def save_state(
    redis: Redis,
    state: WorkflowState,
    ttl: int = DEFAULT_TTL_SECONDS,
) -> None:
    """Serialize and save workflow state to Redis."""
    key = _key(state.session_id)
    data = state.model_dump(mode="json")
    await redis.set(key, json.dumps(data, ensure_ascii=False), ex=ttl)
    logger.debug("workflow_state_saved", session_id=state.session_id)


async def load_state(
    redis: Redis,
    session_id: str,
) -> WorkflowState | None:
    """Load workflow state from Redis. Returns None if not found or expired."""
    key = _key(session_id)
    raw = await redis.get(key)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        return WorkflowState.model_validate(data)
    except Exception as exc:
        logger.warning(
            "workflow_state_load_error",
            session_id=session_id,
            error=str(exc),
        )
        return None


async def delete_state(
    redis: Redis,
    session_id: str,
) -> None:
    """Remove workflow state from Redis."""
    key = _key(session_id)
    await redis.delete(key)
    logger.debug("workflow_state_deleted", session_id=session_id)
