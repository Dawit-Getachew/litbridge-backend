"""Query adapter registry and translation helpers."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from src.ai.adapters.base import BaseQueryAdapter
from src.ai.adapters.clinicaltrials_adapter import ClinicalTrialsAdapter
from src.ai.adapters.europepmc_adapter import EuropePMCAdapter
from src.ai.adapters.openalex_adapter import OpenAlexAdapter
from src.ai.adapters.pubmed_adapter import PubMedAdapter
from src.schemas.enums import QueryType, SearchMode, SourceType
from src.schemas.pico import PICOInput

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from src.ai.llm_client import LLMClient
    from src.core.config import Settings

_ADAPTER_REGISTRY: dict[SourceType, type[BaseQueryAdapter]] = {
    SourceType.PUBMED: PubMedAdapter,
    SourceType.EUROPEPMC: EuropePMCAdapter,
    SourceType.OPENALEX: OpenAlexAdapter,
    SourceType.CLINICALTRIALS: ClinicalTrialsAdapter,
}


def get_adapter(source: SourceType) -> BaseQueryAdapter:
    """Return an adapter instance for the requested source."""
    adapter_type = _ADAPTER_REGISTRY.get(source)
    if adapter_type is None:
        raise ValueError(f"Unsupported source adapter: {source}")
    return adapter_type()


async def translate_for_all_sources(
    query: str,
    query_type: QueryType,
    pico: PICOInput | None = None,
    sources: list[SourceType] | None = None,
    *,
    llm_client: LLMClient | None = None,
    redis_client: Redis | None = None,
    settings: Settings | None = None,
    search_mode: SearchMode | None = None,
) -> dict[SourceType, str]:
    """Translate one query into source-specific variants.

    The Phase 3 LLM rewriter (with Phase C Query2doc expansion) is gated
    to exactly one combination: ``query_type is QueryType.FREE`` AND
    ``search_mode is SearchMode.QUICK``. This is the case the client
    actually complained about — a quick free-text search that should
    surface PubMed-quality results fast. BOOLEAN / PICO queries and the
    deep / light-thinking modes skip the rewriter so their own agentic
    pipelines remain deterministic and reproducible.

    When no ``search_mode`` is provided (legacy callers and unit tests
    that only pass ``query_type``) we fall back to the historical rule:
    rewriter runs when the flag is on AND the query is FREE. This keeps
    every existing test / caller behaving exactly as before so nothing
    breaks in the wild while the new gate is rolled out.
    """
    selected_sources = sources or list(SourceType)
    adapters = [get_adapter(source) for source in selected_sources]

    rewrite_task: asyncio.Task[dict[SourceType, str]] | None = None
    if (
        settings is not None
        and settings.RANKING_LLM_REWRITE
        and query_type is QueryType.FREE
        and llm_client is not None
        and _rewriter_mode_gate_allows(search_mode)
    ):
        from src.ai.query_rewriter import rewrite_for_sources

        rewrite_task = asyncio.create_task(
            rewrite_for_sources(
                query=query,
                sources=selected_sources,
                llm_client=llm_client,
                redis_client=redis_client,
                settings=settings,
            ),
        )

    translated = await asyncio.gather(
        *(adapter.translate(query=query, query_type=query_type, pico=pico) for adapter in adapters),
    )
    rewrites: dict[SourceType, str] = {}
    if rewrite_task is not None:
        try:
            rewrites = await rewrite_task
        except Exception:  # noqa: BLE001 - rewriter already logs; never fail search
            rewrites = {}

    result: dict[SourceType, str] = {}
    for source, fallback in zip(selected_sources, translated, strict=True):
        candidate = rewrites.get(source)
        result[source] = candidate if candidate and candidate.strip() else fallback
    return result


def _rewriter_mode_gate_allows(search_mode: SearchMode | None) -> bool:
    """Return True when the rewriter is permitted for this mode.

    Explicit semantics:
    * ``None`` — caller didn't supply a mode; legacy behavior — allow.
    * ``QUICK`` — primary target mode for the rewriter — allow.
    * anything else (DEEP_*, LIGHT_THINKING) — the workflow graph does
      its own query synthesis; the rewriter would duplicate LLM work.
    """
    if search_mode is None:
        return True
    return search_mode is SearchMode.QUICK


__all__ = ["BaseQueryAdapter", "get_adapter", "translate_for_all_sources"]
