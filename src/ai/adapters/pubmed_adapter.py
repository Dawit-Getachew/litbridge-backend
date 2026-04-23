"""PubMed query translation adapter."""

from __future__ import annotations

import re

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
        """Translate user input to PubMed Boolean expression.

        For ``QueryType.FREE`` we deliberately pass a lightly-sanitized
        natural-language string so PubMed's Automatic Term Mapping (ATM) is
        not bypassed. ATM expands MeSH/synonyms/spelling/plurals — adding
        ``[tiab]``/``[MeSH]`` field tags would *disable* ATM token-by-token
        and narrow recall by 4-10x on biomedical queries.

        BOOLEAN, PICO, and ABSTRACT modes keep their structured behavior so
        PRISMA/PICO contracts are unchanged.
        """
        if query_type is QueryType.BOOLEAN:
            return query.strip()

        if query_type is QueryType.PICO:
            return self._translate_pico(pico, fallback=query)

        if query_type is QueryType.ABSTRACT:
            return self._terms_to_pubmed_boolean(self._extract_keywords(query, max_terms=8))

        return self._sanitize_natural(query)

    @staticmethod
    def _sanitize_natural(query: str) -> str:
        """Return a clean natural-language query safe to feed to PubMed ATM.

        We strip bare ``[`` and ``]`` so user-supplied brackets cannot be
        interpreted as field tags (which would silently disable ATM). All
        other characters — including hyphens (``GLP-1``), apostrophes
        (``Crohn's``) and parentheses — are preserved.
        """
        cleaned = re.sub(r"[\[\]]", " ", query)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

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
