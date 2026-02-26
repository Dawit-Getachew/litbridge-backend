"""PubMed query translation adapter."""

from __future__ import annotations

from src.ai.adapters.base import BaseQueryAdapter
from src.schemas.enums import QueryType, SourceType
from src.schemas.pico import PICOInput


class PubMedAdapter(BaseQueryAdapter):
    """Translate canonical query inputs into PubMed-friendly syntax."""

    source = SourceType.PUBMED

    async def translate(
        self,
        query: str,
        query_type: QueryType,
        pico: PICOInput | None = None,
    ) -> str:
        """Translate user input to PubMed Boolean expression."""
        if query_type is QueryType.BOOLEAN:
            return query.strip()

        if query_type is QueryType.PICO:
            return self._translate_pico(pico, fallback=query)

        if query_type is QueryType.ABSTRACT:
            return self._terms_to_pubmed_boolean(self._extract_keywords(query, max_terms=8))

        return self._terms_to_pubmed_boolean(self._extract_keywords(query, max_terms=6))

    def _translate_pico(self, pico: PICOInput | None, fallback: str) -> str:
        """Build a PICO-constrained PubMed Boolean string."""
        if pico is None:
            return self._terms_to_pubmed_boolean(self._extract_keywords(fallback, max_terms=6))

        components = [pico.population, pico.intervention, pico.comparison, pico.outcome]
        terms = [component.strip() for component in components if component and component.strip()]
        if not terms:
            return self._terms_to_pubmed_boolean(self._extract_keywords(fallback, max_terms=6))

        return " AND ".join(self._build_field_block(term) for term in terms)

    def _terms_to_pubmed_boolean(self, terms: list[str]) -> str:
        """Turn extracted terms into PubMed field-tagged Boolean syntax."""
        if not terms:
            return ""
        return " AND ".join(self._build_field_block(term) for term in terms)

    @staticmethod
    def _build_field_block(term: str) -> str:
        """Wrap one term with title/abstract and MeSH search tags."""
        normalized = f'"{term}"' if " " in term else term
        return f"({normalized}[tiab] OR {normalized}[MeSH])"
