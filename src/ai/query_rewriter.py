"""Optional LLM-driven per-source query rewriter (Phase 3 feature).

Activated server-side via ``Settings.RANKING_LLM_REWRITE`` — never via a
request field — so the frontend cannot trigger it by accident. The rewriter
turns a free-text biomedical query into source-specific variants (PubMed
MeSH-aware, Europe PMC natural, OpenAlex full-text, ClinicalTrials.gov
condition/intervention-focused), then hands them to the existing adapter
pipeline to keep the downstream contract intact.

Behaviour is designed to be strictly additive:

* Disabled by default (``RANKING_LLM_REWRITE = False``).
* Only runs when ``query_type is QueryType.FREE``.
* Results are cached in Redis by a stable hash of the raw query so repeat
  traffic pays the LLM cost at most once per 24h (configurable).
* On any failure — LLM error, timeout, JSON parse error, missing Redis —
  returns ``{}`` so callers silently fall back to the deterministic
  adapter translation. Search latency never regresses catastrophically
  because of this layer.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from src.ai.llm_client import LLMClient
    from src.core.config import Settings

from src.schemas.enums import SourceType

logger = structlog.get_logger(__name__).bind(component="query_rewriter")


_SOURCE_LABELS: dict[SourceType, str] = {
    SourceType.PUBMED: "pubmed",
    SourceType.EUROPEPMC: "europepmc",
    SourceType.OPENALEX: "openalex",
    SourceType.CLINICALTRIALS: "clinicaltrials",
}
_LABEL_TO_SOURCE: dict[str, SourceType] = {label: source for source, label in _SOURCE_LABELS.items()}


_REWRITE_SYSTEM_PROMPT = (
    "You are a biomedical literature retrieval expert. Rewrite the user's "
    "free-text query into one search string per database. Rules:\n"
    "- pubmed: use MeSH terms when natural, keep the query short (<=6 high-signal terms), prefer phrases.\n"
    "- europepmc: natural language; keep synonyms benefit by not over-specifying.\n"
    "- openalex: 3-6 high-signal keywords joined by spaces.\n"
    "- clinicaltrials: focus on the condition and intervention; 2-4 core terms.\n"
    "Preserve the user's clinical intent; do not add claims or outcomes not implied.\n"
    "Return ONLY a JSON object with keys 'pubmed', 'europepmc', 'openalex', 'clinicaltrials'."
)


async def rewrite_for_sources(
    *,
    query: str,
    sources: list[SourceType],
    llm_client: LLMClient,
    redis_client: Redis | None,
    settings: Settings,
) -> dict[SourceType, str]:
    """Return per-source rewrites for ``sources``; ``{}`` on any failure.

    The caller is expected to merge the returned dict over the adapter
    fallbacks (adapter output wins when a given source is missing from the
    rewrite). Keeping this contract simple means enabling/disabling the
    feature is a zero-risk env flip — callers never need to branch on
    whether the LLM succeeded.
    """
    normalized = query.strip()
    if not normalized:
        return {}

    cache_key = _cache_key(normalized, settings)

    cached = await _load_cached(redis_client=redis_client, cache_key=cache_key)
    if cached:
        return _filter_to_requested_sources(cached, sources)

    try:
        rewrites = await asyncio.wait_for(
            _call_llm(query=normalized, llm_client=llm_client, settings=settings),
            timeout=settings.RANKING_LLM_REWRITE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning("llm_rewrite_timeout", query_hash=cache_key)
        return {}
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "llm_rewrite_failed",
            query_hash=cache_key,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return {}

    if not rewrites:
        return {}

    await _store_cached(
        redis_client=redis_client,
        cache_key=cache_key,
        rewrites=rewrites,
        settings=settings,
    )
    return _filter_to_requested_sources(rewrites, sources)


async def _call_llm(
    *,
    query: str,
    llm_client: LLMClient,
    settings: Settings,  # noqa: ARG001 - reserved for future knobs
) -> dict[SourceType, str]:
    """Send the prompt and parse a strict JSON reply into source rewrites."""
    payload = {
        "model": llm_client.model,
        "messages": [
            {"role": "system", "content": _REWRITE_SYSTEM_PROMPT},
            {"role": "user", "content": f"Query: {query}"},
        ],
        "temperature": 0.1,
        "max_tokens": 300,
        "response_format": {"type": "json_object"},
    }
    response = await llm_client.client.post(
        f"{llm_client.base_url}/chat/completions",
        json=payload,
        headers=llm_client._headers(),  # noqa: SLF001 - intentional reuse
        timeout=30.0,
    )
    if response.status_code >= 400:
        return {}

    content = llm_client._extract_message_content(response.json())  # noqa: SLF001
    if not content:
        return {}

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return {}

    if not isinstance(parsed, dict):
        return {}

    rewrites: dict[SourceType, str] = {}
    for label, value in parsed.items():
        source = _LABEL_TO_SOURCE.get(str(label).strip().lower())
        if source is None:
            continue
        if isinstance(value, str) and value.strip():
            rewrites[source] = value.strip()
    return rewrites


def _cache_key(normalized_query: str, settings: Settings) -> str:
    """Stable, collision-resistant cache key for the raw query text."""
    digest = hashlib.sha256(normalized_query.encode("utf-8")).hexdigest()
    return f"litbridge:llm_rewrite:{settings.RANKING_VERSION}:{digest}"


async def _load_cached(
    *,
    redis_client: Redis | None,
    cache_key: str,
) -> dict[SourceType, str] | None:
    if redis_client is None:
        return None
    try:
        raw = await redis_client.get(cache_key)
    except Exception as exc:  # noqa: BLE001
        logger.debug("llm_rewrite_cache_get_failed", error_type=type(exc).__name__)
        return None
    if not raw:
        return None
    try:
        parsed = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None

    rewrites: dict[SourceType, str] = {}
    for label, value in parsed.items():
        source = _LABEL_TO_SOURCE.get(str(label).strip().lower())
        if source is None:
            continue
        if isinstance(value, str) and value.strip():
            rewrites[source] = value.strip()
    return rewrites or None


async def _store_cached(
    *,
    redis_client: Redis | None,
    cache_key: str,
    rewrites: dict[SourceType, str],
    settings: Settings,
) -> None:
    if redis_client is None or not rewrites:
        return
    serializable = {_SOURCE_LABELS[source]: text for source, text in rewrites.items()}
    try:
        await redis_client.set(
            cache_key,
            json.dumps(serializable),
            ex=settings.RANKING_LLM_REWRITE_TTL_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("llm_rewrite_cache_set_failed", error_type=type(exc).__name__)


def _filter_to_requested_sources(
    rewrites: dict[SourceType, str],
    sources: list[SourceType],
) -> dict[SourceType, str]:
    allowed = set(sources)
    return {source: text for source, text in rewrites.items() if source in allowed}
