"""Search session repository for persistence and cursor pagination."""

from __future__ import annotations

import base64
import json
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, func, or_, select
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

    async def count_user_sessions(self, user_id: UUID) -> int:
        """Return the total number of search sessions owned by a user."""
        stmt = (
            select(func.count())
            .select_from(SearchSession)
            .where(SearchSession.user_id == user_id)
        )
        return int((await self.db.execute(stmt)).scalar_one() or 0)

    async def list_user_sessions_by_cursor(
        self,
        user_id: UUID,
        limit: int = 20,
        cursor: str | None = None,
    ) -> tuple[list[SearchSession], str | None]:
        """List search sessions using keyset pagination ordered by recency."""
        stmt = select(SearchSession).where(SearchSession.user_id == user_id)

        decoded_cursor = self._decode_history_cursor(cursor)
        if decoded_cursor is not None:
            cursor_updated, cursor_created, cursor_id = decoded_cursor
            stmt = stmt.where(
                or_(
                    SearchSession.updated_at < cursor_updated,
                    and_(
                        SearchSession.updated_at == cursor_updated,
                        SearchSession.created_at < cursor_created,
                    ),
                    and_(
                        SearchSession.updated_at == cursor_updated,
                        SearchSession.created_at == cursor_created,
                        SearchSession.id < cursor_id,
                    ),
                )
            )

        stmt = stmt.order_by(
            SearchSession.updated_at.desc(),
            SearchSession.created_at.desc(),
            SearchSession.id.desc(),
        ).limit(limit + 1)

        rows = list((await self.db.execute(stmt)).scalars().all())
        has_next = len(rows) > limit
        sessions = rows[:limit]

        next_cursor: str | None = None
        if has_next and sessions:
            last = sessions[-1]
            next_cursor = self._encode_history_cursor(
                updated_at=last.updated_at,
                created_at=last.created_at,
                session_id=last.id,
            )

        return sessions, next_cursor

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

    @staticmethod
    def _encode_history_cursor(
        *,
        updated_at: datetime,
        created_at: datetime,
        session_id: UUID,
    ) -> str:
        payload = json.dumps(
            {
                "u": updated_at.isoformat(),
                "c": created_at.isoformat(),
                "i": str(session_id),
            },
            separators=(",", ":"),
        ).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("utf-8")

    @staticmethod
    def _decode_history_cursor(
        cursor: str | None,
    ) -> tuple[datetime, datetime, UUID] | None:
        if not cursor:
            return None

        try:
            payload = base64.urlsafe_b64decode(cursor.encode("utf-8")).decode("utf-8")
            data = json.loads(payload)
            updated_at = datetime.fromisoformat(str(data["u"]))
            created_at = datetime.fromisoformat(str(data["c"]))
            session_id = UUID(str(data["i"]))
            return updated_at, created_at, session_id
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            return None
