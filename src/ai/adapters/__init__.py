"""Query adapter registry and translation helpers."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from src.ai.adapters.base import BaseQueryAdapter
from src.ai.adapters.clinicaltrials_adapter import ClinicalTrialsAdapter
from src.ai.adapters.europepmc_adapter import EuropePMCAdapter
from src.ai.adapters.openalex_adapter import OpenAlexAdapter
from src.ai.adapters.pubmed_adapter import PubMedAdapter
from src.schemas.enums import QueryType, SourceType
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
) -> dict[SourceType, str]:
    """Translate one query into source-specific variants.

    When ``settings.RANKING_LLM_REWRITE`` is True, ``query_type is FREE``,
    and an ``llm_client`` is supplied, the raw query is first expanded into
    per-source rewrites by the Phase 3 query rewriter. Any rewrites returned
    overlay the deterministic adapter output; sources the rewriter does not
    cover — or any caller that omits the optional params — get the normal
    adapter translation. This keeps the feature strictly additive: the
    public signature of ``translate_for_all_sources`` only grew keyword-only
    optional parameters, so existing callers are unaffected.
    """
    selected_sources = sources or list(SourceType)
    adapters = [get_adapter(source) for source in selected_sources]

    rewrite_task: asyncio.Task[dict[SourceType, str]] | None = None
    if (
        settings is not None
        and settings.RANKING_LLM_REWRITE
        and query_type is QueryType.FREE
        and llm_client is not None
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


__all__ = ["BaseQueryAdapter", "get_adapter", "translate_for_all_sources"]
