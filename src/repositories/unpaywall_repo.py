"""Unpaywall repository for open-access PDF lookups by DOI."""

from __future__ import annotations

import asyncio
import json
import random
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
import structlog
from redis import RedisError
from redis.asyncio import Redis

from src.core.config import Settings, get_settings
from src.core.redis import build_cache_key
from src.schemas.enums import OAStatus


class UnpaywallRepository:
    """Resolve OA status and PDF links from Unpaywall by DOI."""

    BASE_URL = "https://api.unpaywall.org/v2"
    CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
    REQUEST_TIMEOUT = 30.0
    MAX_RETRIES = 3

    def __init__(
        self,
        client: httpx.AsyncClient,
        redis_client: Redis,
        settings: Settings | None = None,
    ) -> None:
        self.client = client
        self.redis_client = redis_client
        self.settings = settings or get_settings()
        self.logger = structlog.get_logger(__name__).bind(repository="unpaywall")

    async def get_oa_url(self, doi: str) -> tuple[OAStatus, str | None]:
        """Return OA status and PDF URL from Unpaywall for a DOI."""
        normalized_doi = self._normalize_doi(doi)
        if normalized_doi is None:
            return OAStatus.UNKNOWN, None

        cache_key = build_cache_key("unpaywall", normalized_doi)
        cached = await self._cache_get(cache_key)
        if cached is not None:
            return cached

        response = await self._request(
            method="GET",
            url=f"{self.BASE_URL}/{normalized_doi}",
            params={"email": self.settings.CONTACT_EMAIL},
        )
        if response is None:
            return OAStatus.UNKNOWN, None

        if response.status_code == 404:
            result = (OAStatus.CLOSED, None)
            await self._cache_set(cache_key, *result)
            return result

        if response.status_code >= 400:
            self.logger.warning(
                "unpaywall_lookup_failed",
                status_code=response.status_code,
                doi=normalized_doi,
            )
            return OAStatus.UNKNOWN, None

        try:
            payload = response.json()
        except ValueError:
            return OAStatus.UNKNOWN, None

        if not isinstance(payload, dict):
            return OAStatus.UNKNOWN, None

        best_oa_location = payload.get("best_oa_location")
        pdf_url = None
        if isinstance(best_oa_location, dict):
            maybe_pdf_url = best_oa_location.get("url_for_pdf")
            if isinstance(maybe_pdf_url, str):
                cleaned = maybe_pdf_url.strip()
                pdf_url = cleaned or None

        is_oa = payload.get("is_oa")
        status = OAStatus.OPEN if pdf_url or bool(is_oa) else OAStatus.CLOSED
        result = (status, pdf_url)
        await self._cache_set(cache_key, *result)
        return result

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response | None:
        """Issue a resilient API request with retry/backoff handling."""
        kwargs.setdefault("timeout", self.REQUEST_TIMEOUT)

        for attempt in range(self.MAX_RETRIES + 1):
            start = time.perf_counter()
            try:
                response = await self.client.request(method=method, url=url, **kwargs)
            except httpx.HTTPError as exc:
                if attempt >= self.MAX_RETRIES:
                    self.logger.warning(
                        "unpaywall_request_error",
                        url=url,
                        error=str(exc),
                    )
                    return None
                await self._sleep_backoff(attempt)
                continue

            duration_ms = int((time.perf_counter() - start) * 1000)
            self.logger.info(
                "unpaywall_request",
                method=method.upper(),
                url=url,
                status_code=response.status_code,
                duration_ms=duration_ms,
            )

            if response.status_code == 429:
                if attempt >= self.MAX_RETRIES:
                    return response
                await asyncio.sleep(self._parse_retry_after(response.headers.get("Retry-After")))
                continue

            if 500 <= response.status_code < 600:
                if attempt >= self.MAX_RETRIES:
                    return response
                await self._sleep_backoff(attempt)
                continue

            return response

        return None

    async def _cache_get(self, key: str) -> tuple[OAStatus, str | None] | None:
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

        status_value = payload.get("oa_status")
        try:
            oa_status = OAStatus(status_value)
        except ValueError:
            return None

        pdf_url = payload.get("pdf_url")
        if not isinstance(pdf_url, str):
            pdf_url = None
        else:
            pdf_url = pdf_url.strip() or None

        return oa_status, pdf_url

    async def _cache_set(self, key: str, oa_status: OAStatus, pdf_url: str | None) -> None:
        payload = {
            "oa_status": oa_status.value,
            "pdf_url": pdf_url,
        }
        try:
            await self.redis_client.set(
                key,
                json.dumps(payload).encode("utf-8"),
                ex=self.CACHE_TTL_SECONDS,
            )
        except (RedisError, TypeError, ValueError):
            return None

    async def _sleep_backoff(self, attempt: int) -> None:
        jitter = random.uniform(0.0, 0.25)  # nosec: non-security jitter for retries
        await asyncio.sleep((2**attempt) + jitter)

    def _parse_retry_after(self, header_value: str | None) -> float:
        default_delay = 1.0
        max_delay = 60.0
        if not header_value:
            return default_delay

        try:
            seconds = float(header_value)
            return max(0.0, min(seconds, max_delay))
        except ValueError:
            pass

        try:
            retry_dt = parsedate_to_datetime(header_value)
            if retry_dt.tzinfo is None:
                retry_dt = retry_dt.replace(tzinfo=UTC)
            delay = (retry_dt - datetime.now(UTC)).total_seconds()
            return max(0.0, min(delay, max_delay))
        except (TypeError, ValueError):
            return default_delay

    def _normalize_doi(self, doi: str) -> str | None:
        value = doi.strip()
        if not value:
            return None

        lowered = value.lower()
        for prefix in ("https://doi.org/", "http://doi.org/", "doi.org/", "doi:"):
            if lowered.startswith(prefix):
                value = value[len(prefix) :]
                break

        normalized = value.strip()
        return normalized or None
