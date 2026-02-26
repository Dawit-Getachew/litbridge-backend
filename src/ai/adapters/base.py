"""Shared base abstractions and text utilities for query adapters."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections import Counter
from typing import ClassVar

from src.schemas.enums import QueryType, SourceType
from src.schemas.pico import PICOInput


class BaseQueryAdapter(ABC):
    """Base contract for translating a user query per source."""

    source: ClassVar[SourceType]

    _STOP_WORDS: ClassVar[set[str]] = {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "does",
        "for",
        "from",
        "how",
        "in",
        "into",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "to",
        "what",
        "when",
        "where",
        "which",
        "with",
        "without",
        "study",
        "trial",
        "effect",
        "effects",
        "result",
        "results",
        "analysis",
        "based",
        "using",
    }

    @abstractmethod
    async def translate(
        self,
        query: str,
        query_type: QueryType,
        pico: PICOInput | None = None,
    ) -> str:
        """Translate a query into the adapter's source-specific dialect."""

    def _extract_keywords(self, text: str, max_terms: int = 5) -> list[str]:
        """Extract frequent informative terms while preserving quoted phrases."""
        cleaned = text.strip()
        if not cleaned:
            return []

        quoted_phrases = [phrase.strip() for phrase in re.findall(r'"([^"]+)"', cleaned)]
        stripped = re.sub(r'"[^"]+"', " ", cleaned)
        stripped = re.sub(r"\[[^\]]+\]", " ", stripped)
        stripped = re.sub(r"[(){}\[\],;:.!?/\\]+", " ", stripped)

        tokens = [
            token.lower()
            for token in re.findall(r"[a-zA-Z][a-zA-Z0-9\-]{1,}", stripped)
            if token.lower() not in self._STOP_WORDS
        ]
        frequency = Counter(tokens)

        ranked_tokens = [
            token
            for token, _count in sorted(
                frequency.items(),
                key=lambda pair: (-pair[1], -len(pair[0]), pair[0]),
            )
        ]

        combined: list[str] = []
        seen: set[str] = set()
        for phrase in quoted_phrases + ranked_tokens:
            normalized = phrase.lower().strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            combined.append(phrase.strip())
            if len(combined) >= max_terms:
                break

        return combined
