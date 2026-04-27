"""Optional LLM-driven per-source query rewriter (Phase 3 feature).

Activated server-side via ``Settings.RANKING_LLM_REWRITE`` — never via a
request field — so the frontend cannot trigger it by accident. The rewriter
turns a free-text biomedical query into source-specific variants (PubMed
MeSH-aware, Europe PMC natural, OpenAlex full-text, ClinicalTrials.gov
condition/intervention-focused), then hands them to the existing adapter
pipeline to keep the downstream contract intact.

Behaviour is designed to be strictly additive:

* Disabled by default (``RANKING_LLM_REWRITE = False``).
* Only runs when ``query_type is QueryType.FREE``.
* Results are cached in Redis by a stable hash of the raw query so repeat
  traffic pays the LLM cost at most once per 24h (configurable).
* On any failure — LLM error, timeout, JSON parse error, missing Redis —
  returns ``{}`` so callers silently fall back to the deterministic
  adapter translation. Search latency never regresses catastrophically
  because of this layer.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import Counter
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from src.ai.llm_client import LLMClient
    from src.core.config import Settings

from src.schemas.enums import SourceType

logger = structlog.get_logger(__name__).bind(component="query_rewriter")


_SOURCE_LABELS: dict[SourceType, str] = {
    SourceType.PUBMED: "pubmed",
    SourceType.EUROPEPMC: "europepmc",
    SourceType.OPENALEX: "openalex",
    SourceType.CLINICALTRIALS: "clinicaltrials",
}
_LABEL_TO_SOURCE: dict[str, SourceType] = {label: source for source, label in _SOURCE_LABELS.items()}


_REWRITE_SYSTEM_PROMPT = (
    "You are a biomedical literature retrieval expert. Rewrite the user's "
    "free-text query into one search string per database AND sketch a short "
    "pseudo-abstract that an ideal answer would have.\n"
    "Rules:\n"
    "- pubmed: use MeSH terms when natural, keep the query short (<=6 high-signal terms), prefer phrases.\n"
    "- europepmc: natural language; keep synonyms benefit by not over-specifying.\n"
    "- openalex: 3-6 high-signal keywords joined by spaces.\n"
    "- clinicaltrials: focus on the condition and intervention; 2-4 core terms.\n"
    "- pseudo_doc: 2-4 sentence synthetic abstract describing the ideal paper that would answer the query.\n"
    "  Do NOT fabricate statistics, effect sizes, or specific outcomes. Stay general and on-topic.\n"
    "Preserve the user's clinical intent; do not add claims or outcomes not implied.\n"
    "Return ONLY a JSON object with keys 'pubmed', 'europepmc', 'openalex', 'clinicaltrials', 'pseudo_doc'."
)


# Pattern + stop-word list for extracting Query2doc expansion terms out of the
# LLM's pseudo-abstract. Mirrors the biomedical-friendly trimmed stop-word set
# used in ``src/ai/adapters/base.py`` so expansions stay on-topic.
_PSEUDO_DOC_TOKEN_PATTERN: re.Pattern[str] = re.compile(r"[a-z0-9\-]{3,}")
_PSEUDO_DOC_STOP_WORDS: frozenset[str] = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "been", "being", "but",
        "by", "can", "could", "do", "does", "for", "from", "had", "has",
        "have", "how", "however", "in", "into", "is", "it", "its", "may",
        "more", "not", "of", "on", "or", "our", "per", "since", "such",
        "that", "the", "their", "then", "there", "these", "they", "this",
        "those", "to", "was", "we", "were", "what", "when", "where",
        "which", "while", "who", "why", "will", "with", "without", "would",
        "you", "your", "study", "studies", "paper", "papers", "abstract",
        "abstracts", "research", "result", "results", "effect", "effects",
        "finding", "findings", "show", "shows", "showed", "shown", "some",
        "evidence",
    }
)


async def rewrite_for_sources(
    *,
    query: str,
    sources: list[SourceType],
    llm_client: LLMClient,
    redis_client: Redis | None,
    settings: Settings,
) -> dict[SourceType, str]:
    """Return per-source rewrites for ``sources``; ``{}`` on any failure.

    The caller is expected to merge the returned dict over the adapter
    fallbacks (adapter output wins when a given source is missing from the
    rewrite). Keeping this contract simple means enabling/disabling the
    feature is a zero-risk env flip — callers never need to branch on
    whether the LLM succeeded.

    When ``settings.RANKING_QUERY2DOC_ENABLED`` is True, the rewriter also
    asks the LLM for a short synthetic abstract and appends the top
    high-IDF-ish terms from it (OR-joined) to each per-source query. This
    is the Query2doc expansion shown to lift biomedical retrieval nDCG
    by 10-25% in the NFCorpus / TREC-COVID / SciFact papers. The
    expansion is cached together with the per-source rewrites so hitting
    the cache remains a pure (non-LLM) path.
    """
    normalized = query.strip()
    if not normalized:
        return {}

    cache_key = _cache_key(normalized, settings)

    cached = await _load_cached(redis_client=redis_client, cache_key=cache_key)
    if cached:
        return _filter_to_requested_sources(cached, sources)

    try:
        rewrites = await asyncio.wait_for(
            _call_llm(query=normalized, llm_client=llm_client, settings=settings),
            timeout=settings.RANKING_LLM_REWRITE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning("llm_rewrite_timeout", query_hash=cache_key)
        return {}
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "llm_rewrite_failed",
            query_hash=cache_key,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return {}

    if not rewrites:
        return {}

    await _store_cached(
        redis_client=redis_client,
        cache_key=cache_key,
        rewrites=rewrites,
        settings=settings,
    )
    return _filter_to_requested_sources(rewrites, sources)


async def _call_llm(
    *,
    query: str,
    llm_client: LLMClient,
    settings: Settings,
) -> dict[SourceType, str]:
    """Send the prompt and parse a strict JSON reply into source rewrites.

    When ``settings.RANKING_QUERY2DOC_ENABLED`` is True the LLM is also
    expected to emit a ``pseudo_doc`` field whose high-signal terms are
    appended (OR-joined) to every per-source rewrite.
    """
    payload = {
        "model": llm_client.model,
        "messages": [
            {"role": "system", "content": _REWRITE_SYSTEM_PROMPT},
            {"role": "user", "content": f"Query: {query}"},
        ],
        "temperature": 0.1,
        "max_tokens": 450,
        "response_format": {"type": "json_object"},
    }
    response = await llm_client.client.post(
        f"{llm_client.base_url}/chat/completions",
        json=payload,
        headers=llm_client._headers(),  # noqa: SLF001 - intentional reuse
        timeout=30.0,
    )
    if response.status_code >= 400:
        return {}

    content = llm_client._extract_message_content(response.json())  # noqa: SLF001
    if not content:
        return {}

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return {}

    if not isinstance(parsed, dict):
        return {}

    rewrites: dict[SourceType, str] = {}
    for label, value in parsed.items():
        source = _LABEL_TO_SOURCE.get(str(label).strip().lower())
        if source is None:
            continue
        if isinstance(value, str) and value.strip():
            rewrites[source] = value.strip()

    pseudo_doc = parsed.get("pseudo_doc")
    if (
        getattr(settings, "RANKING_QUERY2DOC_ENABLED", False)
        and isinstance(pseudo_doc, str)
        and pseudo_doc.strip()
    ):
        expansion_terms = _extract_expansion_terms(query=query, pseudo_doc=pseudo_doc)
        if expansion_terms:
            for source, current in list(rewrites.items()):
                rewrites[source] = _apply_expansion(
                    source=source, base_query=current, terms=expansion_terms,
                )

    return rewrites


def _extract_expansion_terms(
    *, query: str, pseudo_doc: str, max_terms: int = 5,
) -> list[str]:
    """Return up to ``max_terms`` high-signal tokens from the pseudo-abstract.

    Tokens already present in the raw query are dropped (the LLM's
    abstract usually echoes them; appending them again inflates IDF
    without adding signal). Stop-words and generic research filler are
    filtered; longer tokens rank higher at equal frequency so
    multi-syllable biomedical terms (``cardiovascular``, ``pharmacokinetic``)
    win over short function words.
    """
    query_tokens = {
        token
        for token in _PSEUDO_DOC_TOKEN_PATTERN.findall(query.lower())
        if token not in _PSEUDO_DOC_STOP_WORDS
    }
    counts: Counter[str] = Counter()
    for token in _PSEUDO_DOC_TOKEN_PATTERN.findall(pseudo_doc.lower()):
        if token in _PSEUDO_DOC_STOP_WORDS:
            continue
        if token in query_tokens:
            continue
        counts[token] += 1
    if not counts:
        return []
    ranked = sorted(
        counts.items(),
        key=lambda pair: (-pair[1], -len(pair[0]), pair[0]),
    )
    return [token for token, _ in ranked[:max_terms]]


def _apply_expansion(
    *, source: SourceType, base_query: str, terms: list[str],
) -> str:
    """Append OR-joined expansion terms in a syntax each source understands.

    * PubMed expects square-bracketed field tags; we append an
      ``AND ( term1[tiab] OR term2[tiab] ... )`` clause.
    * Europe PMC supports boolean operators natively — same AND/OR shape
      without field tags.
    * OpenAlex / CT.gov use free-text scoring, so we just space-concat the
      expansion terms to feed them to the source's full-text matcher.

    On any input we truncate the expansion set at 5 terms (already done
    upstream, but defensive second guard here) to keep query strings well
    under each source's length caps.
    """
    if not terms or not base_query.strip():
        return base_query
    safe_terms = terms[:5]
    if source is SourceType.PUBMED:
        or_clause = " OR ".join(f"{term}[tiab]" for term in safe_terms)
        return f"({base_query}) AND ({or_clause})"
    if source is SourceType.EUROPEPMC:
        or_clause = " OR ".join(safe_terms)
        return f"({base_query}) AND ({or_clause})"
    # OpenAlex and CT.gov: space-separated free-text scoring.
    return f"{base_query} {' '.join(safe_terms)}"


def _cache_key(normalized_query: str, settings: Settings) -> str:
    """Stable, collision-resistant cache key for the raw query text."""
    digest = hashlib.sha256(normalized_query.encode("utf-8")).hexdigest()
    return f"litbridge:llm_rewrite:{settings.RANKING_VERSION}:{digest}"


async def _load_cached(
    *,
    redis_client: Redis | None,
    cache_key: str,
) -> dict[SourceType, str] | None:
    if redis_client is None:
        return None
    try:
        raw = await redis_client.get(cache_key)
    except Exception as exc:  # noqa: BLE001
        logger.debug("llm_rewrite_cache_get_failed", error_type=type(exc).__name__)
        return None
    if not raw:
        return None
    try:
        parsed = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None

    rewrites: dict[SourceType, str] = {}
    for label, value in parsed.items():
        source = _LABEL_TO_SOURCE.get(str(label).strip().lower())
        if source is None:
            continue
        if isinstance(value, str) and value.strip():
            rewrites[source] = value.strip()
    return rewrites or None


async def _store_cached(
    *,
    redis_client: Redis | None,
    cache_key: str,
    rewrites: dict[SourceType, str],
    settings: Settings,
) -> None:
    if redis_client is None or not rewrites:
        return
    serializable = {_SOURCE_LABELS[source]: text for source, text in rewrites.items()}
    try:
        await redis_client.set(
            cache_key,
            json.dumps(serializable),
            ex=settings.RANKING_LLM_REWRITE_TTL_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("llm_rewrite_cache_set_failed", error_type=type(exc).__name__)


def _filter_to_requested_sources(
    rewrites: dict[SourceType, str],
    sources: list[SourceType],
) -> dict[SourceType, str]:
    allowed = set(sources)
    return {source: text for source, text in rewrites.items() if source in allowed}
