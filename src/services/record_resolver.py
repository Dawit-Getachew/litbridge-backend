"""Natural language record resolver — maps free-text paper references to UnifiedRecords.

Pure function module with no I/O or side effects.  Uses rapidfuzz for fuzzy
matching and simple regex for positional / author+year references.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from src.schemas.records import UnifiedRecord

FUZZY_TITLE_THRESHOLD = 70
KEYWORD_OVERLAP_THRESHOLD = 0.3
MAX_RESOLVED = 5

_ORDINAL_MAP: dict[str, int] = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "last": -1,
}

_POSITIONAL_RE = re.compile(
    r"(?:the\s+)?(?:(?P<ordinal>" + "|".join(_ORDINAL_MAP) + r")"
    r"|(?:(?:paper|article|result|study)\s*(?:#|number|num\.?)?\s*(?P<number>\d+)))"
    r"(?:\s+(?:paper|article|result|study))?",
    re.IGNORECASE,
)

_AUTHOR_YEAR_RE = re.compile(
    r"(?P<author>[A-Z][a-z]{1,30})"
    r"(?:\s+(?:et\s+al\.?\s*,?\s*)?)"
    r"(?P<year>(?:19|20)\d{2})",
)


@dataclass
class _ScoredRecord:
    record: UnifiedRecord
    score: float
    method: str


def resolve_references(
    message: str,
    records: list[UnifiedRecord],
) -> list[UnifiedRecord]:
    """Resolve natural language paper references to actual records.

    Applies resolution strategies in order of specificity:
      1. Positional ("the first paper", "paper #3")
      2. Author + year ("Zhang 2024", "Smith et al. 2023")
      3. Fuzzy title match
      4. Keyword overlap

    Returns a deduplicated list of matched records (up to MAX_RESOLVED).
    An empty list means no confident match — the caller should fall back to
    including the full record list in LLM context.
    """
    if not records or not message.strip():
        return []

    seen_ids: set[str] = set()
    scored: list[_ScoredRecord] = []

    # Step 1: Positional references
    for match in _POSITIONAL_RE.finditer(message):
        ordinal_word = match.group("ordinal")
        number_str = match.group("number")

        if ordinal_word:
            position = _ORDINAL_MAP.get(ordinal_word.lower())
        elif number_str:
            position = int(number_str)
        else:
            continue

        if position is None:
            continue

        if position == -1:
            idx = len(records) - 1
        else:
            idx = position - 1

        if 0 <= idx < len(records):
            record = records[idx]
            if record.id not in seen_ids:
                seen_ids.add(record.id)
                scored.append(_ScoredRecord(record=record, score=100.0, method="positional"))

    # Step 2: Author + year references
    for match in _AUTHOR_YEAR_RE.finditer(message):
        author_fragment = match.group("author").lower()
        year = int(match.group("year"))

        for record in records:
            if record.id in seen_ids:
                continue
            if record.year != year:
                continue
            author_match = any(
                author_fragment in author.lower()
                for author in record.authors
            )
            if author_match:
                seen_ids.add(record.id)
                scored.append(_ScoredRecord(record=record, score=95.0, method="author_year"))

    # Step 3: Fuzzy title match (token_set_ratio handles partial overlaps well)
    message_lower = message.lower()
    for record in records:
        if record.id in seen_ids:
            continue
        ratio = fuzz.token_set_ratio(message_lower, record.title.lower())
        if ratio >= FUZZY_TITLE_THRESHOLD:
            seen_ids.add(record.id)
            scored.append(_ScoredRecord(record=record, score=ratio, method="fuzzy_title"))

    # Step 4: Keyword overlap (uses the smaller set as denominator to handle
    # short queries against long titles, e.g. "RECOVERY trial" vs a 10-word title)
    message_tokens = _tokenize(message)
    if message_tokens and not scored:
        for record in records:
            if record.id in seen_ids:
                continue
            title_tokens = _tokenize(record.title)
            if not title_tokens:
                continue
            common = message_tokens & title_tokens
            if not common:
                continue
            denominator = min(len(message_tokens), len(title_tokens))
            overlap = len(common) / denominator
            if overlap >= KEYWORD_OVERLAP_THRESHOLD:
                seen_ids.add(record.id)
                scored.append(_ScoredRecord(record=record, score=overlap * 80, method="keyword"))

    scored.sort(key=lambda s: s.score, reverse=True)
    return [s.record for s in scored[:MAX_RESOLVED]]


_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "in", "on", "for", "to", "with",
    "is", "are", "was", "were", "this", "that", "it", "its", "from", "by",
    "at", "as", "be", "has", "have", "had", "not", "but", "about", "can",
    "do", "does", "did", "will", "would", "should", "could", "may", "might",
    "paper", "article", "study", "research", "trial", "results", "explain",
    "compare", "deep", "dive", "tell", "me", "more", "what", "how", "why",
    "please", "find", "related",
})


def _tokenize(text: str) -> set[str]:
    """Extract meaningful lowercase word tokens from text."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS}
