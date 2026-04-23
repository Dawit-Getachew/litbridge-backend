"""OpenAlex query translation adapter."""

from __future__ import annotations

import re

from src.ai.adapters.base import BaseQueryAdapter
from src.schemas.enums import QueryType, SourceType
from src.schemas.pico import PICOInput


class OpenAlexAdapter(BaseQueryAdapter):
    """Translate user queries into OpenAlex-friendly Boolean syntax."""

    source = SourceType.OPENALEX

    async def translate(
        self,
        query: str,
        query_type: QueryType,
        pico: PICOInput | None = None,
    ) -> str:
        """Convert query into OpenAlex-compatible syntax.

        For ``QueryType.FREE`` we pass a sanitized natural-language string
        directly to OpenAlex. The ``search`` API parameter (set at the
        repository layer) performs BM25-style scoring across title and
        abstract with stop-word handling — wrapping every keyword in a
        forced ``AND`` chain (the previous behavior) artificially narrowed
        the result set and discarded the citation ranking signal.
        """
        if query_type is QueryType.PICO:
            terms = self._terms_from_pico(pico)
            return self._join_terms(terms)

        if query_type is QueryType.ABSTRACT:
            phrases = self._extract_noun_phrases(query, max_terms=6)
            return self._join_terms(phrases)

        if query_type is QueryType.BOOLEAN:
            return self._normalize_boolean(query)

        return self._sanitize_natural(query)

    @staticmethod
    def _sanitize_natural(query: str) -> str:
        """Return a clean natural-language query for OpenAlex's search param.

        Strips PubMed-style field tags (``[tiab]``, ``[MeSH]``) and bare
        brackets if a user accidentally pastes them, then collapses
        whitespace. Hyphens, apostrophes, and parentheses are preserved so
        compound entities (``GLP-1``, ``Crohn's``) survive intact.
        """
        cleaned = re.sub(r"\[[\w: ]+\]", " ", query)
        cleaned = re.sub(r"[\[\]]", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    def _normalize_boolean(self, query: str) -> str:
        """Remove unsupported PubMed tags and normalize operators."""
        stripped = re.sub(r"\[[\w: ]+\]", "", query)
        stripped = re.sub(r"\s+", " ", stripped).strip()
        stripped = re.sub(r"\b(and|or|not)\b", lambda match: match.group(1).upper(), stripped, flags=re.IGNORECASE)
        return stripped

    def _terms_from_pico(self, pico: PICOInput | None) -> list[str]:
        """Extract meaningful search terms from PICO components."""
        if pico is None:
            return []

        raw = [pico.population, pico.intervention, pico.comparison, pico.outcome]
        terms: list[str] = []
        for component in raw:
            if not component or not component.strip():
                continue
            terms.extend(self._extract_keywords(component, max_terms=2))

        seen: set[str] = set()
        deduped: list[str] = []
        for term in terms:
            key = term.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(term)
        return deduped

    def _extract_noun_phrases(self, text: str, max_terms: int) -> list[str]:
        """Approximate noun-phrase extraction with adjacent content words."""
        cleaned = self._normalize_boolean(text)
        quoted = [phrase.strip() for phrase in re.findall(r'"([^"]+)"', cleaned)]

        words = [word.lower() for word in re.findall(r"[a-zA-Z][a-zA-Z0-9\-]{2,}", cleaned)]
        phrases: list[str] = []
        for idx in range(len(words) - 1):
            left, right = words[idx], words[idx + 1]
            if left in self._STOP_WORDS or right in self._STOP_WORDS:
                continue
            phrase = f"{left} {right}"
            if phrase not in phrases:
                phrases.append(phrase)

        merged = quoted + phrases + self._extract_keywords(cleaned, max_terms=max_terms)
        unique: list[str] = []
        seen: set[str] = set()
        for term in merged:
            key = term.lower().strip()
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(term.strip())
            if len(unique) >= max_terms:
                break
        return unique

    @staticmethod
    def _join_terms(terms: list[str]) -> str:
        """Join plain terms as an OpenAlex Boolean query."""
        if not terms:
            return ""
        normalized = [f'"{term}"' if " " in term else term for term in terms]
        return " AND ".join(normalized)
