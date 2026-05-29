"""Repository for the lithub_sync_outbox table."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.lithub_sync_outbox import LitHubSyncOutbox


_MAX_ATTEMPTS = 5


def _backoff_seconds(attempts: int) -> int:
    """1m → 2m → 4m → 8m → 16m exponential, capped at 30 min."""
    return min(60 * (2**attempts), 30 * 60)


class LitHubSyncRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def enqueue(self, user_id: UUID, payload: dict, error: str | None = None) -> LitHubSyncOutbox:
        row = LitHubSyncOutbox(
            user_id=user_id,
            payload=payload,
            last_error=error,
            status="pending",
            next_attempt_at=datetime.now(UTC),
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def fetch_due(self, limit: int = 50) -> list[LitHubSyncOutbox]:
        now = datetime.now(UTC)
        stmt = (
            select(LitHubSyncOutbox)
            .where(
                LitHubSyncOutbox.status == "pending",
                LitHubSyncOutbox.next_attempt_at <= now,
            )
            .order_by(LitHubSyncOutbox.next_attempt_at)
            .limit(limit)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def mark_sent(self, row: LitHubSyncOutbox) -> None:
        row.status = "sent"
        row.last_attempt_at = datetime.now(UTC)
        await self.db.commit()

    async def mark_failed(self, row: LitHubSyncOutbox, error: str) -> None:
        row.attempts = (row.attempts or 0) + 1
        row.last_error = error[:512]
        row.last_attempt_at = datetime.now(UTC)
        if row.attempts >= _MAX_ATTEMPTS:
            row.status = "dead"
        else:
            row.next_attempt_at = datetime.now(UTC) + timedelta(
                seconds=_backoff_seconds(row.attempts),
            )
        await self.db.commit()
