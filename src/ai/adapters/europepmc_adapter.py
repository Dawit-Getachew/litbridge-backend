"""Europe PMC query translation adapter."""

from __future__ import annotations

from src.ai.adapters.pubmed_adapter import PubMedAdapter
from src.schemas.enums import QueryType, SourceType
from src.schemas.pico import PICOInput


class EuropePMCAdapter(PubMedAdapter):
    """Europe PMC accepts nearly identical syntax to PubMed.

    For ``QueryType.FREE`` we pass a lightly-sanitized natural-language
    string. Europe PMC's ``synonym=true`` API parameter (set at the
    repository layer) expands MeSH/UMLS synonyms over the same controlled
    vocabularies PubMed's ATM uses, which is the main reason we want
    natural language here rather than tagged Boolean blocks.

    BOOLEAN/PICO/ABSTRACT modes inherit the PubMed adapter's structured
    behavior unchanged.
    """

    source = SourceType.EUROPEPMC

    async def translate(
        self,
        query: str,
        query_type: QueryType,
        pico: PICOInput | None = None,
    ) -> str:
        """Delegate to PubMed adapter; FREE returns natural-language string."""
        return await super().translate(query=query, query_type=query_type, pico=pico)
