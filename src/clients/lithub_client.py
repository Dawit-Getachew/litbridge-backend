"""HTTP client for the Scienthesis LitHub Service."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx
import structlog

from src.core.config import Settings, get_settings
from src.clients.service_token import mint_service_token

logger = structlog.get_logger(__name__)


class LitHubClientError(Exception):
    def __init__(self, status_code: int, detail: Any) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"LitHub {status_code}: {detail}")


class LitHubUpstreamError(Exception):
    """LitHub unreachable or 5xx."""


class LitHubClient:
    """Async client for LitHub user-facing and internal endpoints."""

    def __init__(self, http_client: httpx.AsyncClient, settings: Settings | None = None) -> None:
        self._client = http_client
        self._settings = settings or get_settings()
        # Tolerate an unset base at construction so the client can be created
        # in dependency wiring without crashing an un-configured deploy; an
        # actual request with no base raises at call time instead.
        self._base = (self._settings.LITHUB_BASE_URL or "").rstrip("/")

    def _require_base(self) -> str:
        if not self._base:
            raise LitHubUpstreamError("LITHUB_BASE_URL is not configured.")
        return self._base

    # ── User-token endpoints ────────────────────────────────────────

    async def save_paper(
        self, access_token: str, body: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/v1/library/save",
            json=body,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    async def list_library(
        self, access_token: str, *, params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            "/api/v1/library",
            params=params or {},
            headers={"Authorization": f"Bearer {access_token}"},
        )

    async def get_papers_bulk(
        self, access_token: str, paper_ids: list[UUID | str],
    ) -> list[dict[str, Any]]:
        if not paper_ids:
            return []
        body = await self._request(
            "POST",
            "/api/v1/papers/bulk",
            json={"paper_ids": [str(pid) for pid in paper_ids]},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        return body.get("papers", []) if isinstance(body, dict) else []

    # ── Internal (service-token) endpoints ─────────────────────────

    async def internal_save_paper(
        self,
        user_id: UUID,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        """Service-to-service single save on behalf of a user (Identity sub).

        Calls LitHub's dedicated ``/internal/library/save`` which returns the
        canonical ``paper_id`` so the caller can persist the cross-service link.
        """
        return await self._internal_post(
            "/api/v1/internal/library/save",
            {"user_id": str(user_id), "item": body},
        )

    async def internal_list_library(
        self,
        user_id: UUID,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Read a user's central LitHub library (keyed by Identity sub).

        Lets LitPortal surface papers saved from LitPulse (and its own
        collection saves) in one unified list, via the service-token internal
        endpoint — works regardless of the inbound token type.
        """
        q: dict[str, Any] = {"user_id": str(user_id)}
        if params:
            q.update({k: v for k, v in params.items() if v is not None})
        return await self._internal_get("/api/v1/internal/library", params=q)

    async def internal_papers_bulk(
        self, paper_ids: list[UUID | str],
    ) -> list[dict[str, Any]]:
        """Fetch canonical paper metadata by id (service token)."""
        if not paper_ids:
            return []
        result = await self._internal_post(
            "/api/v1/internal/papers/bulk",
            {"paper_ids": [str(pid) for pid in paper_ids]},
        )
        return result.get("papers", []) if isinstance(result, dict) else []

    async def internal_bulk_import(
        self,
        user_id: UUID,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return await self._internal_post(
            "/api/v1/internal/library/bulk-import",
            {"user_id": str(user_id), "items": items},
        )

    async def internal_membership(
        self,
        user_id: UUID,
        *,
        pmid: str | None = None,
        doi: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"user_id": str(user_id)}
        if pmid:
            params["pmid"] = pmid
        if doi:
            params["doi"] = doi
        return await self._internal_get(
            "/api/v1/internal/library/membership", params=params,
        )

    # ── Helpers ────────────────────────────────────────────────────

    async def _internal_get(
        self, path: str, params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        token = mint_service_token("scienthesis-lithub")
        return await self._request(
            "GET", path, params=params, headers={"X-Service-Token": token},
        )

    async def _internal_post(
        self, path: str, body: dict[str, Any],
    ) -> dict[str, Any]:
        token = mint_service_token("scienthesis-lithub")
        return await self._request(
            "POST", path, json=body, headers={"X-Service-Token": token},
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._require_base()}{path}"
        try:
            resp = await self._client.request(
                method, url, json=json, params=params, headers=headers,
            )
        except httpx.HTTPError as exc:
            logger.error(
                "lithub_request_transport_failed",
                path=path, method=method, error=str(exc),
            )
            raise LitHubUpstreamError(
                f"Could not reach LitHub at {url}: {exc}",
            ) from exc

        if resp.status_code >= 500:
            raise LitHubUpstreamError(
                f"LitHub returned {resp.status_code}: {resp.text[:200]}",
            )
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except ValueError:
                detail = resp.text
            raise LitHubClientError(status_code=resp.status_code, detail=detail)
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()
