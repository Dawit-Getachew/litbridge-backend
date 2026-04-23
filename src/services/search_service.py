"""Search service orchestration for fast-path A->B->C workflow."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID

import structlog
from redis.asyncio import Redis

from src.ai.llm_client import LLMClient
from src.core.exceptions import SearchNotFoundError
from src.repositories.search_repo import SearchRepository
from src.schemas.enums import QueryType, SourceType, SearchMode
from src.schemas.records import PaginatedResults, UnifiedRecord
from src.schemas.search import (
    SearchHistoryItem,
    SearchHistoryResponse,
    SearchRequest,
    SearchResponse,
    SearchStatusResponse,
)
from src.services.dedup_service import DedupService
from src.services.enrichment_service import EnrichmentService
from src.services.fetcher_service import FetcherService
from src.services.oa_service import OAService
from src.services.pico_fill_service import fill_missing_pico
from src.services.prisma_service import PrismaService


class SearchService:
    """Coordinate fetch, dedup, persistence, and read APIs for search."""

    def __init__(
        self,
        fetcher: FetcherService,
        dedup: DedupService,
        prisma: PrismaService,
        search_repo: SearchRepository,
        redis_client: Redis,
        enrichment_service: EnrichmentService,
        oa_service: OAService,
        llm_client: LLMClient | None = None,
    ) -> None:
        self.fetcher = fetcher
        self.dedup = dedup
        self.prisma = prisma
        self.search_repo = search_repo
        self.redis_client = redis_client
        self.enrichment_service = enrichment_service
        self.oa_service = oa_service
        self.llm_client = llm_client
        self._background_tasks: set[asyncio.Task[None]] = set()
        self.logger = structlog.get_logger(__name__).bind(service="search_service")

    async def execute_search(
        self,
        request: SearchRequest,
        user_id: str | None = None,
    ) -> SearchResponse:
        """Run A->B->C fast path and persist the resulting session data."""
        if (
            request.query_type is QueryType.PICO
            and request.pico is not None
            and self.llm_client is not None
        ):
            request.pico = await fill_missing_pico(request.pico, self.llm_client)

        from uuid import UUID as _UUID
        uid = _UUID(user_id) if user_id else None
        session = await self.search_repo.create_session(request, user_id=uid)
        selected_sources = request.sources or list(SourceType)

        try:
            raw_records, source_counts, failed_sources = await self.fetcher.fetch_all_sources(
                query=request.query,
                query_type=request.query_type,
                search_mode=request.search_mode,
                sources=selected_sources,
                pico=request.pico,
                max_results=request.max_results,
            )

            unified_records = self.dedup.deduplicate(
                raw_records,
                query=request.query,
                query_type=request.query_type,
            )
            await self.search_repo.store_results(str(session.id), unified_records)

            session.status = "completed"
            session.total_identified = len(raw_records)
            session.total_after_dedup = len(unified_records)
            session.sources_completed = [
                source.value
                for source in selected_sources
                if source not in failed_sources and source_counts.get(source, 0) >= 0
            ]
            session.sources_failed = [source.value for source in failed_sources]
            session.completed_at = datetime.now(timezone.utc)
            await self.search_repo.update_session(session)

            if request.search_mode in {SearchMode.DEEP_ANALYZE, SearchMode.DEEP_THINKING}:
                task = asyncio.create_task(
                    self._run_background_enrichment(str(session.id), unified_records),
                )
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)

            self.logger.info(
                "search_completed",
                search_id=str(session.id),
                total_identified=session.total_identified,
                total_after_dedup=session.total_after_dedup,
                failed_sources=session.sources_failed,
            )
            return SearchResponse(search_id=str(session.id))
        except Exception:
            session.status = "failed"
            session.completed_at = datetime.now(timezone.utc)
            await self.search_repo.update_session(session)
            raise

    async def get_results(self, search_id: str, cursor: str | None) -> PaginatedResults:
        """Return one cursor-paginated results page for an existing search."""
        session = await self.search_repo.get_session(search_id)
        if session is None:
            raise SearchNotFoundError(search_id)

        records, next_cursor = await self.search_repo.get_results_page(search_id, cursor)
        total_count = session.total_after_dedup or len(session.results or [])
        return PaginatedResults(
            search_id=search_id,
            total_count=total_count,
            records=records,
            next_cursor=next_cursor,
        )

    async def get_search_status(self, search_id: str) -> SearchStatusResponse:
        """Return persisted status metadata for a search session."""
        session = await self.search_repo.get_session(search_id)
        if session is None:
            raise SearchNotFoundError(search_id)

        sources_completed = self._parse_sources(session.sources_completed)
        sources_failed = self._parse_sources(session.sources_failed)
        total_count = session.total_after_dedup or len(session.results or [])
        progress_pct = 100 if session.status == "completed" else 0

        return SearchStatusResponse(
            search_id=search_id,
            status=session.status,
            total_count=total_count,
            sources_completed=sources_completed,
            sources_failed=sources_failed,
            progress_pct=progress_pct,
        )

    async def list_user_search_history(
        self,
        user_id: str,
        *,
        limit: int = 20,
        cursor: str | None = None,
    ) -> SearchHistoryResponse:
        """Return cursor-paginated search history for one authenticated user."""
        uid = UUID(user_id)
        sessions, next_cursor = await self.search_repo.list_user_sessions_by_cursor(
            uid,
            limit=limit,
            cursor=cursor,
        )
        total = await self.search_repo.count_user_sessions(uid)
        return SearchHistoryResponse(
            searches=[
                SearchHistoryItem(
                    id=session.id,
                    query=session.query,
                    query_type=session.query_type,
                    search_mode=session.search_mode,
                    sources=session.sources or [],
                    status=session.status,
                    total_after_dedup=session.total_after_dedup,
                    created_at=session.created_at,
                    updated_at=session.updated_at,
                )
                for session in sessions
            ],
            total=total,
            next_cursor=next_cursor,
        )

    @staticmethod
    def _parse_sources(raw_sources: list[str] | None) -> list[SourceType]:
        parsed: list[SourceType] = []
        for source_value in raw_sources or []:
            try:
                parsed.append(SourceType(source_value))
            except ValueError:
                continue
        return parsed

    async def _run_background_enrichment(
        self,
        search_id: str,
        records: list[UnifiedRecord],
    ) -> None:
        """Run enrichment/OA jobs in the background and persist merged updates."""
        indexed_records = [record.model_copy(deep=True) for record in records]
        index_by_id = {record.id: idx for idx, record in enumerate(indexed_records)}

        enrichment_task = asyncio.create_task(self.enrichment_service.enrich_batch(indexed_records))
        oa_task = asyncio.create_task(self.oa_service.resolve_batch(indexed_records))
        pending: set[asyncio.Task] = {enrichment_task, oa_task}

        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for completed in done:
                try:
                    if completed is enrichment_task:
                        enrichments = completed.result()
                        for enrichment in enrichments:
                            idx = index_by_id.get(enrichment.id)
                            if idx is None:
                                continue
                            indexed_records[idx].tldr = enrichment.tldr
                            indexed_records[idx].citation_count = enrichment.citation_count
                            if enrichment.oa_status is not None:
                                indexed_records[idx].oa_status = enrichment.oa_status
                            indexed_records[idx].pdf_url = enrichment.pdf_url
                        await self.search_repo.store_results(search_id, indexed_records)
                        continue

                    oa_results = completed.result()
                    for record_id, (oa_status, pdf_url) in oa_results.items():
                        idx = index_by_id.get(record_id)
                        if idx is None:
                            continue
                        indexed_records[idx].oa_status = oa_status
                        indexed_records[idx].pdf_url = pdf_url
                    await self.search_repo.store_results(search_id, indexed_records)
                except Exception as exc:
                    self.logger.warning(
                        "background_enrichment_task_failed",
                        search_id=search_id,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
