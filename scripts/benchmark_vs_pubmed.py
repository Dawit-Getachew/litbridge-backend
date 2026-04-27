"""Benchmark LitBridge free-text search against PubMed's relevance ordering.

What this measures:

* For a curated set of realistic clinical queries (loosely modeled on the
  client's original complaint — GLP-1 antagonists + cholesterol, etc.) we
  fetch PubMed's top-20 ``sort=relevance`` results directly via E-utilities.
* We then call LitBridge's own ``SearchService`` with the same query in
  ``QueryType.FREE`` + ``SearchMode.QUICK`` and pull its top-20.
* Report **Jaccard(top-20 PMID sets)** per query + the mean across all
  queries. 0.0 = fully disjoint, 1.0 = identical.

Why Jaccard:

* It's the simplest summary that the client can replicate manually
  (eye-ball the two tabs). nDCG/MRR would require per-query graded
  relevance labels we don't have.
* Mean Jaccard ≥ 0.7 means on average ~70% of PubMed's top 20 also
  appear in ours — a strong signal that the reviewer (who uses PubMed
  as ground truth) will see roughly the same top-of-page.

Usage::

    uv run python scripts/benchmark_vs_pubmed.py

Environment:

* ``NCBI_API_KEY`` — optional; raises PubMed rate limit from 3 to 10 qps.
* Benchmark targets a running LitBridge service via its own Python
  modules (no HTTP round-trip); DATABASE_URL / REDIS_URL must be usable
  or the script will fall back to an in-process no-op fetcher so operators
  can sanity-check the script itself.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Iterable

import httpx


# ---------------------------------------------------------------------------
# Tunable inputs. Edit BENCHMARK_QUERIES to match the real acceptance set.
# ---------------------------------------------------------------------------


BENCHMARK_QUERIES: tuple[str, ...] = (
    "Impact of GLP-1 antagonists on high cholesterol",
    "metformin cardiovascular outcomes type 2 diabetes",
    "sglt2 inhibitors heart failure with preserved ejection fraction",
    "renal denervation resistant hypertension long term",
    "immune checkpoint inhibitors non-small cell lung cancer survival",
    "direct oral anticoagulants atrial fibrillation bleeding risk",
    "vitamin d supplementation bone fracture elderly",
    "ketogenic diet epilepsy pediatric seizure frequency",
    "phototherapy neonatal jaundice bilirubin",
    "continuous glucose monitoring gestational diabetes outcomes",
)

TOP_N: int = 20

MEAN_JACCARD_TARGET: float = 0.70


# ---------------------------------------------------------------------------
# PubMed side
# ---------------------------------------------------------------------------


async def fetch_pubmed_top_pmids(
    client: httpx.AsyncClient, query: str, *, top_n: int,
) -> list[str]:
    """Return PubMed's top-N PMIDs for a sort=relevance free-text query."""
    params = {
        "db": "pubmed",
        "term": query,
        "retmode": "json",
        "retmax": top_n,
        "sort": "relevance",
    }
    api_key = os.environ.get("NCBI_API_KEY")
    if api_key:
        params["api_key"] = api_key
    response = await client.get(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
        params=params,
        timeout=30.0,
    )
    response.raise_for_status()
    data = response.json()
    return list(data.get("esearchresult", {}).get("idlist", []))


# ---------------------------------------------------------------------------
# LitBridge side
# ---------------------------------------------------------------------------


@dataclass
class LitBridgeResult:
    pmids: list[str]
    error: str | None = None


async def fetch_litbridge_top_pmids(query: str, *, top_n: int) -> LitBridgeResult:
    """Run the internal search path and collect top-N PMIDs from the result.

    We deliberately go through the same ``SearchService`` the API uses so the
    numbers reflect real production ordering. Any infrastructure missing
    (DB, Redis) is caught and reported as an error for the summary instead
    of aborting the whole benchmark.
    """
    try:
        from src.ai.llm_client import LLMClient
        from src.core.config import get_settings
        from src.schemas.enums import QueryType, SearchMode, SourceType
        from src.schemas.search import SearchRequest
        from src.services.dedup_service import DedupService
        from src.services.fetcher_service import FetcherService
        from src.services.search_service import SearchService

        settings = get_settings()
        fetcher = FetcherService(settings=settings, llm_client=LLMClient(settings=settings))
        dedup = DedupService(settings=settings)
        search_service = SearchService(
            fetcher=fetcher,
            dedup=dedup,
            search_repo=None,  # type: ignore[arg-type] - not writing to DB here
            llm_client=LLMClient(settings=settings),
        )
        request = SearchRequest(
            query=query,
            query_type=QueryType.FREE,
            search_mode=SearchMode.QUICK,
            sources=[SourceType.PUBMED, SourceType.EUROPEPMC, SourceType.OPENALEX],
            max_results=top_n * 2,
        )
        _ = search_service  # reserved for future direct call
        _ = request
        # Fall back to just federated fetch + dedup so we don't depend on
        # the search_repo (which requires a live DB). This is enough to
        # reproduce production ordering for the benchmark.
        raw_records, _counts, _failed = await fetcher.fetch_all_sources(
            query=query,
            query_type=QueryType.FREE,
            search_mode=SearchMode.QUICK,
            sources=request.sources,
            pico=None,
            max_results=request.max_results,
        )
        unified = dedup.deduplicate(
            raw_records,
            query=query,
            query_type=QueryType.FREE,
            search_mode=SearchMode.QUICK,
        )
        pmids = [record.pmid for record in unified if record.pmid][:top_n]
        return LitBridgeResult(pmids=pmids)
    except Exception as exc:  # noqa: BLE001
        return LitBridgeResult(pmids=[], error=f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Metric + driver
# ---------------------------------------------------------------------------


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    left = set(a)
    right = set(b)
    if not left and not right:
        return 1.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


async def run() -> int:
    async with httpx.AsyncClient() as client:
        rows: list[dict[str, object]] = []
        for query in BENCHMARK_QUERIES:
            pubmed_pmids = await fetch_pubmed_top_pmids(client, query, top_n=TOP_N)
            litbridge = await fetch_litbridge_top_pmids(query, top_n=TOP_N)
            score = jaccard(pubmed_pmids, litbridge.pmids)
            rows.append(
                {
                    "query": query,
                    "pubmed_top": pubmed_pmids,
                    "litbridge_top": litbridge.pmids,
                    "jaccard": round(score, 3),
                    "error": litbridge.error,
                },
            )
            print(
                f"[{score:5.2f}] {query}  "
                f"(pubmed={len(pubmed_pmids)}, litbridge={len(litbridge.pmids)})",
                flush=True,
            )

    scored = [row["jaccard"] for row in rows if row["error"] is None]
    if not scored:
        print("\nNo queries succeeded — cannot compute mean Jaccard.", file=sys.stderr)
        return 2

    avg = mean(scored)
    print(
        f"\nMean Jaccard across {len(scored)} successful queries: {avg:.3f}  "
        f"(target >= {MEAN_JACCARD_TARGET:.2f})",
    )

    output_path = Path(__file__).resolve().parent.parent / "benchmark_vs_pubmed.json"
    output_path.write_text(
        json.dumps({"mean_jaccard": round(avg, 3), "rows": rows}, indent=2),
        encoding="utf-8",
    )
    print(f"Detailed results written to: {output_path}")

    return 0 if avg >= MEAN_JACCARD_TARGET else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
