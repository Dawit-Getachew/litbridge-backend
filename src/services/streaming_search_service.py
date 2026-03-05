"""Streaming search service that emits granular, ChatGPT-style SSE progress events."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from datetime import datetime, timezone

import structlog
from redis.asyncio import Redis

from src.ai.adapters import translate_for_all_sources
from src.repositories import get_repository
from src.repositories.search_repo import SearchRepository
from src.schemas.enrichment import EnrichmentResponse
from src.schemas.enums import OAStatus, SearchMode, SourceType
from src.schemas.records import RawRecord, UnifiedRecord
from src.schemas.search import SearchRequest
from src.schemas.streaming import StreamEvent
from src.services.dedup_service import DedupService
from src.services.enrichment_service import EnrichmentService
from src.services.fetcher_service import FetcherService
from src.services.oa_service import OAService
from src.services.prisma_service import PrismaService

_SOURCE_LABELS: dict[SourceType, str] = {
    SourceType.PUBMED: "PubMed",
    SourceType.EUROPEPMC: "Europe PMC",
    SourceType.OPENALEX: "OpenAlex",
    SourceType.CLINICALTRIALS: "ClinicalTrials.gov",
}


def _label(source: SourceType) -> str:
    return _SOURCE_LABELS.get(source, source.value)


class StreamingSearchService:
    """Coordinate fetch, dedup, and persistence while streaming progress events."""

    def __init__(
        self,
        fetcher: FetcherService,
        dedup: DedupService,
        prisma: PrismaService,
        search_repo: SearchRepository,
        redis_client: Redis,
        enrichment_service: EnrichmentService,
        oa_service: OAService,
    ) -> None:
        self.fetcher = fetcher
        self.dedup = dedup
        self.prisma = prisma
        self.search_repo = search_repo
        self.redis_client = redis_client
        self.enrichment_service = enrichment_service
        self.oa_service = oa_service
        self.logger = structlog.get_logger(__name__).bind(service="streaming_search_service")

    async def execute_search_stream(
        self,
        request: SearchRequest,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Run federated search and yield granular progress updates as SSE events."""
        session = await self.search_repo.create_session(request)
        search_id = str(session.id)
        selected_sources = request.sources or list(SourceType)

        yield StreamEvent(
            event="search_started",
            data={
                "search_id": search_id,
                "sources": [source.value for source in selected_sources],
                "search_mode": request.search_mode.value,
            },
        )

        try:
            # --- Phase 1: Translate and fetch --------------------------------
            yield StreamEvent(event="status", data={"message": "Translating query for each database..."})

            translated_queries = await translate_for_all_sources(
                query=request.query,
                query_type=request.query_type,
                pico=request.pico,
                sources=selected_sources,
            )
            effective_max_results = FetcherService._max_results_for_mode(
                request.search_mode,
                request.max_results,
            )

            all_raw_records: list[RawRecord] = []
            source_counts: dict[SourceType, int] = {s: 0 for s in selected_sources}
            failed_sources: list[SourceType] = []

            source_label_str = ", ".join(_label(s) for s in selected_sources)
            yield StreamEvent(
                event="status",
                data={"message": f"Searching {source_label_str}..."},
            )

            # Emit source_searching before each fetch starts
            for source in selected_sources:
                yield StreamEvent(
                    event="source_searching",
                    data={"source": source.value, "message": f"Searching {_label(source)}..."},
                )

            tasks = [
                asyncio.create_task(
                    self._fetch_source(
                        source=source,
                        translated_query=translated_queries.get(source, request.query),
                        max_results=effective_max_results,
                    )
                )
                for source in selected_sources
            ]

            for completed_task in asyncio.as_completed(tasks):
                source, records, duration_ms, error = await completed_task
                if error is not None:
                    failed_sources.append(source)
                    yield StreamEvent(
                        event="source_failed",
                        data={"source": source.value, "error": error},
                    )
                    yield StreamEvent(
                        event="status",
                        data={"message": f"{_label(source)} search failed — continuing with other sources."},
                    )
                    continue

                count = len(records)
                source_counts[source] = count
                all_raw_records.extend(records)
                yield StreamEvent(
                    event="source_completed",
                    data={
                        "source": source.value,
                        "count": count,
                        "duration_ms": duration_ms,
                    },
                )
                yield StreamEvent(
                    event="status",
                    data={"message": f"Found {count} articles from {_label(source)}."},
                )

            # --- Phase 2: Dedup -----------------------------------------------
            total_before = len(all_raw_records)
            yield StreamEvent(
                event="status",
                data={"message": f"Removing duplicates from {total_before} total articles..."},
            )

            unified_records = self.dedup.deduplicate(all_raw_records)
            total_after = len(unified_records)
            duplicates_removed = total_before - total_after

            yield StreamEvent(
                event="dedup_completed",
                data={
                    "total_before": total_before,
                    "total_after": total_after,
                    "duplicates_removed": duplicates_removed,
                },
            )
            yield StreamEvent(
                event="status",
                data={"message": f"Removed {duplicates_removed} duplicates — {total_after} unique articles remain."},
            )

            await self.search_repo.store_results(search_id, unified_records)

            # --- Phase 2b: Per-record enrichment (deep modes) -----------------
            if request.search_mode in {SearchMode.DEEP_ANALYZE, SearchMode.DEEP_THINKING}:
                async for event_or_records in self._stream_enrichment_per_record(
                    search_id, unified_records,
                ):
                    if isinstance(event_or_records, StreamEvent):
                        yield event_or_records
                    elif isinstance(event_or_records, list):
                        unified_records = event_or_records

            # --- Phase 3: Session update --------------------------------------
            session.status = "completed"
            session.total_identified = total_before
            session.total_after_dedup = total_after
            session.sources_completed = [
                source.value
                for source in selected_sources
                if source not in failed_sources and source_counts.get(source, 0) >= 0
            ]
            session.sources_failed = [source.value for source in failed_sources]
            session.completed_at = datetime.now(timezone.utc)
            await self.search_repo.update_session(session)

            # --- Phase 4: Thinking (AI synthesis) -----------------------------
            if request.search_mode in {SearchMode.DEEP_THINKING, SearchMode.LIGHT_THINKING}:
                yield StreamEvent(event="status", data={"message": "Analyzing and synthesizing results..."})
                async for chunk in self.enrichment_service.stream_thinking(
                    query=request.query,
                    records=unified_records,
                    mode=request.search_mode,
                ):
                    yield StreamEvent(event="thinking", data={"chunk": chunk})

            yield StreamEvent(
                event="search_completed",
                data={"search_id": search_id, "total_count": total_after},
            )
        except Exception as exc:
            session.status = "failed"
            session.completed_at = datetime.now(timezone.utc)
            await self.search_repo.update_session(session)
            self.logger.exception(
                "streaming_search_failed",
                search_id=search_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            yield StreamEvent(
                event="error",
                data={"error": str(exc) or "Streaming search failed."},
            )

    async def _fetch_source(
        self,
        source: SourceType,
        translated_query: str,
        max_results: int,
    ) -> tuple[SourceType, list[RawRecord], int, str | None]:
        """Fetch one source and return normalized completion metadata."""
        repository = get_repository(source=source, client=self.fetcher.client)
        started_at = time.perf_counter()
        try:
            records = await repository.search(query=translated_query, max_results=max_results)
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            self.logger.warning(
                "stream_source_failed",
                source=source.value,
                duration_ms=duration_ms,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return source, [], duration_ms, str(exc) or type(exc).__name__

        duration_ms = int((time.perf_counter() - started_at) * 1000)
        self.logger.info(
            "stream_source_completed",
            source=source.value,
            count=len(records),
            duration_ms=duration_ms,
        )
        return source, records, duration_ms, None

    async def _stream_enrichment_per_record(
        self,
        search_id: str,
        records: list[UnifiedRecord],
    ) -> AsyncGenerator[StreamEvent | list[UnifiedRecord], None]:
        """Enrich and resolve OA per-record, yielding events as each completes."""
        if not records:
            yield records
            return

        yield StreamEvent(
            event="status",
            data={"message": f"Enriching {len(records)} articles with AI summaries and citations..."},
        )

        enriched_records = [record.model_copy(deep=True) for record in records]
        enrichment_by_id: dict[str, EnrichmentResponse] = {}
        oa_by_id: dict[str, tuple[OAStatus, str | None]] = {}

        # Run enrichment and OA in parallel, streaming enrichment results
        oa_task = asyncio.create_task(self._resolve_oa_batch(search_id, enriched_records))

        completed_count = 0
        async for enrichment in self.enrichment_service.enrich_stream(enriched_records):
            enrichment_by_id[enrichment.id] = enrichment
            completed_count += 1

            yield StreamEvent(
                event="record_enriched",
                data={
                    "id": enrichment.id,
                    "tldr": enrichment.tldr,
                    "citation_count": enrichment.citation_count,
                    "progress": f"{completed_count}/{len(records)}",
                },
            )

        # Wait for OA resolution
        try:
            oa_by_id = await oa_task
        except Exception as exc:
            self.logger.warning(
                "stream_oa_batch_failed",
                search_id=search_id,
                error=str(exc),
            )

        # Merge enrichment + OA into records
        for record in enriched_records:
            enrichment = enrichment_by_id.get(record.id)
            if enrichment is not None:
                record.tldr = enrichment.tldr
                record.citation_count = enrichment.citation_count
                if enrichment.oa_status is not None:
                    record.oa_status = enrichment.oa_status
                record.pdf_url = enrichment.pdf_url

            oa_payload = oa_by_id.get(record.id)
            if oa_payload is not None:
                oa_status, pdf_url = oa_payload
                record.oa_status = oa_status
                record.pdf_url = pdf_url

        await self.search_repo.store_results(search_id, enriched_records)

        yield StreamEvent(
            event="status",
            data={"message": f"Enrichment complete — {completed_count} articles processed."},
        )

        yield enriched_records

    async def _resolve_oa_batch(
        self,
        search_id: str,
        records: list[UnifiedRecord],
    ) -> dict[str, tuple[OAStatus, str | None]]:
        """Resolve OA status for all records, returning results by ID."""
        try:
            return await self.oa_service.resolve_batch(records)
        except Exception as exc:
            self.logger.warning(
                "stream_oa_batch_failed",
                search_id=search_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return {}
