"""API tests for cursor-paginated authenticated search history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

from src.core import deps
from src.repositories.search_repo import SearchRepository
from src.services.search_service import SearchService


@dataclass
class FakeSearchSession:
    """Minimal in-memory search session model for history API tests."""

    id: UUID
    user_id: UUID
    query: str
    query_type: str
    search_mode: str
    sources: list[str]
    status: str
    total_after_dedup: int
    created_at: datetime
    updated_at: datetime


class InMemoryHistoryRepository:
    """In-memory repository implementing the history methods used by SearchService."""

    def __init__(self, sessions: list[FakeSearchSession]) -> None:
        self._sessions = sessions

    async def count_user_sessions(self, user_id: UUID) -> int:
        return len([s for s in self._sessions if s.user_id == user_id])

    async def list_user_sessions_by_cursor(
        self,
        user_id: UUID,
        limit: int = 20,
        cursor: str | None = None,
    ) -> tuple[list[FakeSearchSession], str | None]:
        sessions = [
            s for s in self._sessions
            if s.user_id == user_id
        ]
        sessions.sort(
            key=lambda item: (item.updated_at, item.created_at, item.id),
            reverse=True,
        )

        decoded = SearchRepository._decode_history_cursor(cursor)
        if decoded is not None:
            cursor_updated, cursor_created, cursor_id = decoded
            sessions = [
                s for s in sessions
                if (
                    s.updated_at < cursor_updated
                    or (
                        s.updated_at == cursor_updated
                        and s.created_at < cursor_created
                    )
                    or (
                        s.updated_at == cursor_updated
                        and s.created_at == cursor_created
                        and s.id < cursor_id
                    )
                )
            ]

        page = sessions[:limit]
        next_cursor: str | None = None
        if len(sessions) > limit and page:
            last = page[-1]
            next_cursor = SearchRepository._encode_history_cursor(
                updated_at=last.updated_at,
                created_at=last.created_at,
                session_id=last.id,
            )
        return page, next_cursor


@pytest.mark.asyncio
async def test_get_search_history_orders_by_updated_then_created_and_paginates(
    async_client: AsyncClient,
) -> None:
    """History endpoint should support infinite-scroll via stable cursor pages."""
    from src.main import app

    user_id = uuid4()
    other_user = uuid4()
    now = datetime(2026, 3, 20, 12, 0, 0, tzinfo=timezone.utc)

    top_updated_old_created = FakeSearchSession(
        id=UUID("00000000-0000-0000-0000-000000000001"),
        user_id=user_id,
        query="old search recently updated",
        query_type="free",
        search_mode="quick",
        sources=["pubmed"],
        status="completed",
        total_after_dedup=12,
        created_at=now.replace(hour=8),
        updated_at=now.replace(hour=12, minute=30),
    )
    tie_updated_newer_created = FakeSearchSession(
        id=UUID("00000000-0000-0000-0000-000000000002"),
        user_id=user_id,
        query="newer created tie",
        query_type="free",
        search_mode="quick",
        sources=["pubmed"],
        status="completed",
        total_after_dedup=8,
        created_at=now.replace(hour=11, minute=50),
        updated_at=now.replace(hour=11),
    )
    tie_updated_older_created = FakeSearchSession(
        id=UUID("00000000-0000-0000-0000-000000000003"),
        user_id=user_id,
        query="older created tie",
        query_type="boolean",
        search_mode="deep_research",
        sources=["openalex"],
        status="completed",
        total_after_dedup=7,
        created_at=now.replace(hour=10, minute=45),
        updated_at=now.replace(hour=11),
    )
    oldest = FakeSearchSession(
        id=UUID("00000000-0000-0000-0000-000000000004"),
        user_id=user_id,
        query="oldest",
        query_type="structured",
        search_mode="quick",
        sources=["europepmc"],
        status="failed",
        total_after_dedup=0,
        created_at=now.replace(day=19, hour=9),
        updated_at=now.replace(day=19, hour=9, minute=15),
    )
    ignored_other_user = FakeSearchSession(
        id=UUID("00000000-0000-0000-0000-000000000005"),
        user_id=other_user,
        query="other user",
        query_type="free",
        search_mode="quick",
        sources=["pubmed"],
        status="completed",
        total_after_dedup=99,
        created_at=now,
        updated_at=now,
    )

    repo = InMemoryHistoryRepository(
        [
            tie_updated_older_created,
            ignored_other_user,
            oldest,
            top_updated_old_created,
            tie_updated_newer_created,
        ]
    )
    service = SearchService(
        fetcher=AsyncMock(),
        dedup=AsyncMock(),
        prisma=AsyncMock(),
        search_repo=repo,  # type: ignore[arg-type]
        redis_client=AsyncMock(),
        enrichment_service=AsyncMock(),
        oa_service=AsyncMock(),
    )

    app.dependency_overrides[deps.get_search_service] = lambda: service
    app.dependency_overrides[deps.get_current_user] = lambda: SimpleNamespace(id=user_id)

    try:
        first_page = await async_client.get("/api/v1/search/history", params={"limit": 2})
        first_body = first_page.json()

        assert first_page.status_code == 200
        assert first_body["total"] == 4
        first_ids = [item["id"] for item in first_body["searches"]]
        assert first_ids == [
            str(top_updated_old_created.id),
            str(tie_updated_newer_created.id),
        ]
        assert first_body["next_cursor"] is not None

        second_page = await async_client.get(
            "/api/v1/search/history",
            params={"limit": 2, "cursor": first_body["next_cursor"]},
        )
        second_body = second_page.json()

        assert second_page.status_code == 200
        second_ids = [item["id"] for item in second_body["searches"]]
        assert second_ids == [
            str(tie_updated_older_created.id),
            str(oldest.id),
        ]
        assert second_body["next_cursor"] is None
        assert set(first_ids).isdisjoint(second_ids)
    finally:
        app.dependency_overrides.pop(deps.get_search_service, None)
        app.dependency_overrides.pop(deps.get_current_user, None)
