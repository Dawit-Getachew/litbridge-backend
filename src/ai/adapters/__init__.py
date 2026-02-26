"""Query adapter registry and translation helpers."""

from __future__ import annotations

import asyncio

from src.ai.adapters.base import BaseQueryAdapter
from src.ai.adapters.clinicaltrials_adapter import ClinicalTrialsAdapter
from src.ai.adapters.europepmc_adapter import EuropePMCAdapter
from src.ai.adapters.openalex_adapter import OpenAlexAdapter
from src.ai.adapters.pubmed_adapter import PubMedAdapter
from src.schemas.enums import QueryType, SourceType
from src.schemas.pico import PICOInput

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
) -> dict[SourceType, str]:
    """Translate one query into source-specific variants."""
    selected_sources = sources or list(SourceType)
    adapters = [get_adapter(source) for source in selected_sources]

    translated = await asyncio.gather(
        *(adapter.translate(query=query, query_type=query_type, pico=pico) for adapter in adapters),
    )
    return {source: transformed for source, transformed in zip(selected_sources, translated, strict=True)}


__all__ = ["BaseQueryAdapter", "get_adapter", "translate_for_all_sources"]
