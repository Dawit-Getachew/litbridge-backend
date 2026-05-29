"""Cross-service sync helper: write to LitHub, fall back to outbox on failure."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from src.clients.lithub_client import LitHubClient, LitHubClientError, LitHubUpstreamError
from src.repositories.lithub_sync_repo import LitHubSyncRepository

logger = structlog.get_logger(__name__)


class LitHubSyncService:
    """Best-effort dual-write coordinator for the LitPortal BFF.

    Callers invoke :meth:`save_paper` from inside a request that has already
    committed the LitPortal-local write (the collection item, the search-row
    reference, etc.). This method then attempts the LitHub-side save and, on
    any failure, persists the payload in the outbox for a sweeper to retry —
    so a transient LitHub outage never produces a half-saved user state.
    """

    def __init__(
        self,
        *,
        lithub: LitHubClient,
        outbox: LitHubSyncRepository,
    ) -> None:
        self._lithub = lithub
        self._outbox = outbox

    async def save_paper(
        self,
        identity_user_id: UUID,
        payload: dict[str, Any],
        *,
        access_token: str | None = None,
    ) -> tuple[bool, dict[str, Any] | None]:
        """Save ``payload`` to LitHub keyed by the user's Identity ``sub``.

        ``identity_user_id`` is ALWAYS the Identity ``sub`` (the platform-wide
        user id), never the litbridge-local user id — this is what makes a
        paper saved here match what LitPulse reads for the same user. Returns
        ``(ok, response)``; on any LitHub error the payload is enqueued in the
        outbox for the background sweeper to retry, so a save is never lost.
        """
        try:
            if access_token:
                response = await self._lithub.save_paper(access_token, payload)
            else:
                response = await self._lithub.internal_save_paper(identity_user_id, payload)
            return True, response
        except (LitHubUpstreamError, LitHubClientError) as exc:
            error_message = str(exc)
            await self._outbox.enqueue(identity_user_id, payload, error=error_message)
            logger.warning(
                "lithub_save_failed_enqueued_outbox",
                identity_user_id=str(identity_user_id),
                error=error_message,
            )
            return False, None

    async def drain_outbox(self, batch_size: int = 50) -> int:
        """Retry pending outbox rows. Returns the count of successful sends."""
        due = await self._outbox.fetch_due(limit=batch_size)
        sent = 0
        for row in due:
            try:
                await self._lithub.internal_save_paper(row.user_id, dict(row.payload))
                await self._outbox.mark_sent(row)
                sent += 1
            except (LitHubUpstreamError, LitHubClientError) as exc:
                await self._outbox.mark_failed(row, str(exc))
            except Exception as exc:  # noqa: BLE001
                await self._outbox.mark_failed(row, f"unexpected: {exc}")
        return sent
