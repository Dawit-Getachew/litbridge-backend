"""ClinicalTrials.gov query translation adapter."""

from __future__ import annotations

import re

from src.ai.adapters.base import BaseQueryAdapter
from src.schemas.enums import QueryType, SourceType
from src.schemas.pico import PICOInput


class ClinicalTrialsAdapter(BaseQueryAdapter):
    """Aggressively simplify queries for ClinicalTrials.gov V2 search."""

    source = SourceType.CLINICALTRIALS

    async def translate(
        self,
        query: str,
        query_type: QueryType,
        pico: PICOInput | None = None,
    ) -> str:
        """Translate input into a short keyword query."""
        if query_type is QueryType.PICO:
            terms = self._extract_pico_terms(pico, fallback=query)
            return self._to_keyword_query(terms, min_terms=2, max_terms=5)

        if query_type is QueryType.ABSTRACT:
            terms = self._extract_condition_intervention_terms(query)
            return self._to_keyword_query(terms, min_terms=3, max_terms=5)

        simplified = self._simplify_syntax(query)
        terms = self._extract_keywords(simplified, max_terms=6)
        return self._to_keyword_query(terms, min_terms=3, max_terms=5)

    def _simplify_syntax(self, query: str) -> str:
        """Strip PubMed-specific syntax and flatten grouping."""
        simplified = re.sub(r"\[[\w: ]+\]", " ", query)
        simplified = re.sub(r"[(){}\[\]]", " ", simplified)
        simplified = re.sub(r"\b(and|or|not)\b", " ", simplified, flags=re.IGNORECASE)
        simplified = re.sub(r'"', " ", simplified)
        simplified = re.sub(r"\s+", " ", simplified)
        return simplified.strip()

    def _extract_pico_terms(self, pico: PICOInput | None, fallback: str) -> list[str]:
        """Prioritize population + intervention terms for trial search."""
        if pico is None:
            return self._extract_keywords(self._simplify_syntax(fallback), max_terms=5)

        terms: list[str] = []
        for component in (pico.population, pico.intervention):
            if component and component.strip():
                terms.extend(self._extract_keywords(component, max_terms=3))

        if not terms and fallback.strip():
            return self._extract_keywords(self._simplify_syntax(fallback), max_terms=5)
        return terms

    def _extract_condition_intervention_terms(self, abstract_text: str) -> list[str]:
        """Extract likely condition + intervention keywords from abstract text."""
        simplified = self._simplify_syntax(abstract_text)
        candidates = self._extract_keywords(simplified, max_terms=10)
        if not candidates:
            return []

        condition_markers = {
            "disease",
            "syndrome",
            "disorder",
            "cancer",
            "diabetes",
            "hypertension",
            "asthma",
            "infection",
            "risk",
            "mortality",
            "cardiovascular",
        }
        intervention_markers = {
            "drug",
            "therapy",
            "treatment",
            "metformin",
            "insulin",
            "placebo",
            "dose",
            "surgery",
            "vaccine",
            "intervention",
        }

        prioritized = [
            term
            for term in candidates
            if any(marker in term.lower() for marker in condition_markers | intervention_markers)
        ]
        if len(prioritized) >= 3:
            return prioritized
        return prioritized + [term for term in candidates if term not in prioritized]

    @staticmethod
    def _to_keyword_query(terms: list[str], min_terms: int, max_terms: int) -> str:
        """Convert raw terms into a short AND-joined keyword query."""
        unique: list[str] = []
        seen: set[str] = set()
        for term in terms:
            cleaned = term.strip().lower()
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            unique.append(term.strip())
            if len(unique) >= max_terms:
                break

        if len(unique) < min_terms:
            unique = unique[:max_terms]

        return " AND ".join(unique)
