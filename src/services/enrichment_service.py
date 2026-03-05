"""Background enrichment orchestration for TLDR and citation metadata."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator

import structlog
from redis import RedisError
from redis.asyncio import Redis

from src.ai.llm_client import LLMClient
from src.core.redis import build_cache_key
from src.repositories.semantic_scholar_repo import SemanticScholarRepository
from src.schemas.enrichment import EnrichmentResponse
from src.schemas.enums import SearchMode
from src.schemas.records import UnifiedRecord


class EnrichmentService:
    """Coordinate semantic enrichment using Semantic Scholar and LLM fallback."""

    CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
    BATCH_CONCURRENCY = 10

    def __init__(
        self,
        s2_repo: SemanticScholarRepository,
        llm_client: LLMClient,
        redis_client: Redis,
    ) -> None:
        self.s2_repo = s2_repo
        self.llm_client = llm_client
        self.redis_client = redis_client
        self.logger = structlog.get_logger(__name__).bind(service="enrichment_service")

    async def enrich_record(self, record: UnifiedRecord) -> EnrichmentResponse:
        """Enrich one unified record with TLDR/citation metadata."""
        cache_key = build_cache_key("enrichment", record.id)
        cached = await self._cache_get(cache_key)
        if cached is not None:
            return cached

        paper: dict | None = None
        try:
            paper = await self.s2_repo.get_paper(doi=record.doi, pmid=record.pmid)
        except Exception:
            paper = None

        tldr = self._extract_tldr(paper)
        citation_count = self._extract_citation_count(paper)

        if not tldr and record.abstract:
            try:
                tldr = await self.llm_client.generate_tldr(title=record.title, abstract=record.abstract)
            except Exception:
                tldr = None

        response = EnrichmentResponse(
            id=record.id,
            tldr=tldr,
            citation_count=citation_count,
            oa_status=record.oa_status,
            pdf_url=record.pdf_url,
        )
        await self._cache_set(cache_key, response)
        return response

    async def enrich_batch(self, records: list[UnifiedRecord]) -> list[EnrichmentResponse]:
        """Enrich multiple records with bounded async concurrency."""
        if not records:
            return []

        semaphore = asyncio.Semaphore(self.BATCH_CONCURRENCY)

        async def enrich_with_limit(record: UnifiedRecord) -> EnrichmentResponse:
            async with semaphore:
                return await self.enrich_record(record)

        return list(await asyncio.gather(*(enrich_with_limit(record) for record in records)))

    async def enrich_stream(
        self,
        records: list[UnifiedRecord],
    ) -> AsyncGenerator[EnrichmentResponse, None]:
        """Yield enrichment results one at a time as each record completes.

        Uses bounded concurrency via asyncio tasks and as_completed so the
        caller can emit SSE events progressively rather than waiting for
        the entire batch.
        """
        if not records:
            return

        semaphore = asyncio.Semaphore(self.BATCH_CONCURRENCY)

        async def _enrich(record: UnifiedRecord) -> EnrichmentResponse:
            async with semaphore:
                return await self.enrich_record(record)

        tasks = [asyncio.create_task(_enrich(r)) for r in records]
        for completed in asyncio.as_completed(tasks):
            try:
                result = await completed
                yield result
            except Exception as exc:
                self.logger.warning("enrich_stream_record_failed", error=str(exc))
                continue

    async def stream_thinking(
        self,
        query: str,
        records: list[UnifiedRecord],
        mode: SearchMode,
    ) -> AsyncGenerator[str, None]:
        """Yield thinking output token-by-token for all thinking modes."""
        if mode is SearchMode.DEEP_THINKING:
            async for chunk in self.llm_client.stream_analysis(query=query, records=records):
                if chunk:
                    yield chunk
            return

        if mode is SearchMode.LIGHT_THINKING:
            async for chunk in self.llm_client.stream_quick_summary(query=query, records=records):
                if chunk:
                    yield chunk

    def _extract_tldr(self, paper: dict | None) -> str | None:
        if not isinstance(paper, dict):
            return None
        tldr = paper.get("tldr")
        if isinstance(tldr, dict):
            text = tldr.get("text")
            if isinstance(text, str):
                value = text.strip()
                return value or None
            return None
        if isinstance(tldr, str):
            value = tldr.strip()
            return value or None
        return None

    def _extract_citation_count(self, paper: dict | None) -> int | None:
        if not isinstance(paper, dict):
            return None
        count = paper.get("citationCount")
        if isinstance(count, int):
            return count
        if isinstance(count, str) and count.isdigit():
            return int(count)
        return None

    async def _cache_get(self, key: str) -> EnrichmentResponse | None:
        try:
            cached = await self.redis_client.get(key)
        except RedisError:
            return None
        if not cached:
            return None
        try:
            payload = json.loads(cached.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        try:
            return EnrichmentResponse.model_validate(payload)
        except Exception:
            return None

    async def _cache_set(self, key: str, value: EnrichmentResponse) -> None:
        try:
            payload = value.model_dump(mode="json")
            await self.redis_client.set(
                key,
                json.dumps(payload).encode("utf-8"),
                ex=self.CACHE_TTL_SECONDS,
            )
        except (RedisError, TypeError, ValueError):
            return None
