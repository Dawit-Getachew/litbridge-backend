"""Search session repository for persistence and cursor pagination."""

from __future__ import annotations

import base64
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import SearchSession
from src.schemas.search import SearchRequest
from src.schemas.records import UnifiedRecord


class SearchRepository:
    """Persist and retrieve search sessions and paginated search results."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_session(
        self,
        request: SearchRequest,
        user_id: UUID | None = None,
    ) -> SearchSession:
        """Create a processing search session for a new request."""
        session = SearchSession(
            user_id=user_id,
            query=request.query,
            query_type=request.query_type.value,
            search_mode=request.search_mode.value,
            sources=[source.value for source in (request.sources or [])],
            pico=request.pico.model_dump(mode="json") if request.pico else None,
            status="processing",
            results=[],
            sources_completed=[],
            sources_failed=[],
        )
        self.db.add(session)
        await self.db.commit()
        await self.db.refresh(session)
        return session

    async def list_user_sessions(
        self,
        user_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SearchSession]:
        """List search sessions belonging to a user, newest first."""
        stmt = (
            select(SearchSession)
            .where(SearchSession.user_id == user_id)
            .order_by(SearchSession.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def update_session(self, session: SearchSession) -> None:
        """Persist updates to an existing search session."""
        await self.db.commit()
        await self.db.refresh(session)

    async def get_session(self, search_id: str) -> SearchSession | None:
        """Load one search session by opaque UUID string."""
        session_uuid = self._parse_uuid(search_id)
        if session_uuid is None:
            return None

        stmt = select(SearchSession).where(SearchSession.id == session_uuid)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def store_results(self, search_id: str, records: list[UnifiedRecord]) -> None:
        """Store deduplicated records JSON payload for a search."""
        session = await self.get_session(search_id)
        if session is None:
            return

        session.results = [record.model_dump(mode="json") for record in records]
        session.total_after_dedup = len(records)
        await self.db.commit()
        await self.db.refresh(session)

    async def get_results_page(
        self,
        search_id: str,
        cursor: str | None,
        page_size: int = 20,
    ) -> tuple[list[UnifiedRecord], str | None]:
        """Return one cursor-paginated page from stored session results JSON."""
        session = await self.get_session(search_id)
        if session is None:
            return [], None

        all_results = session.results or []
        offset = self._decode_cursor(cursor)
        if offset < 0:
            offset = 0

        page_data = all_results[offset : offset + page_size]
        records = [UnifiedRecord.model_validate(item) for item in page_data]

        next_offset = offset + len(records)
        next_cursor = None
        if next_offset < len(all_results):
            next_cursor = self._encode_cursor(next_offset)

        return records, next_cursor

    @staticmethod
    def _parse_uuid(raw_value: str) -> UUID | None:
        try:
            return UUID(raw_value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _encode_cursor(offset: int) -> str:
        payload = str(offset).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("utf-8")

    @staticmethod
    def _decode_cursor(cursor: str | None) -> int:
        if cursor is None:
            return 0

        try:
            decoded = base64.urlsafe_b64decode(cursor.encode("utf-8")).decode("utf-8")
            return int(decoded)
        except (ValueError, UnicodeDecodeError):
            return 0
