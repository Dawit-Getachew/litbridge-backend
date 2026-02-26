"""Base repository primitives for external source integrations."""

from __future__ import annotations

import abc
import asyncio
import random
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
import structlog

from src.core.config import Settings, get_settings
from src.core.exceptions import SourceFetchError
from src.schemas.records import RawRecord
from src.schemas.enums import SourceType


class BaseSourceRepository(abc.ABC):
    """Abstract repository for a single external data source."""

    source: SourceType
    max_retries: int = 3
    request_timeout: float = 30.0
    min_request_interval: float = 0.0

    def __init__(self, client: httpx.AsyncClient, settings: Settings | None = None) -> None:
        self.client = client
        self.settings = settings or get_settings()
        self.logger = structlog.get_logger(__name__).bind(source=self.source.value)
        self._rate_limit_lock = asyncio.Lock()
        self._last_request_at = 0.0

    @abc.abstractmethod
    async def search(self, query: str, max_results: int = 100) -> list[RawRecord]:
        """Search this source and return normalized records."""

    @abc.abstractmethod
    async def fetch_by_id(self, source_id: str) -> RawRecord | None:
        """Fetch one record by source-specific identifier."""

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Perform resilient HTTP requests with retry and rate-limit handling."""
        kwargs.setdefault("timeout", self.request_timeout)

        for attempt in range(self.max_retries + 1):
            await self._apply_local_rate_limit()
            start = time.perf_counter()
            self.logger.info(
                "external_request_started",
                method=method.upper(),
                url=url,
                attempt=attempt + 1,
            )
            try:
                response = await self.client.request(method=method, url=url, **kwargs)
            except httpx.TimeoutException as exc:
                duration_ms = int((time.perf_counter() - start) * 1000)
                if attempt >= self.max_retries:
                    self.logger.error(
                        "external_request_timeout_exhausted",
                        method=method.upper(),
                        url=url,
                        duration_ms=duration_ms,
                        error=str(exc),
                    )
                    raise SourceFetchError(
                        source=self.source.value,
                        status_code=504,
                        message=f"Timeout from {self.source.value}",
                    ) from exc
                await self._sleep_backoff(attempt)
                self.logger.warning(
                    "external_request_timeout_retrying",
                    method=method.upper(),
                    url=url,
                    duration_ms=duration_ms,
                    next_attempt=attempt + 2,
                )
                continue
            except httpx.HTTPError as exc:
                duration_ms = int((time.perf_counter() - start) * 1000)
                if attempt >= self.max_retries:
                    self.logger.error(
                        "external_request_error_exhausted",
                        method=method.upper(),
                        url=url,
                        duration_ms=duration_ms,
                        error=str(exc),
                    )
                    raise SourceFetchError(
                        source=self.source.value,
                        status_code=503,
                        message=f"Request error from {self.source.value}",
                    ) from exc
                await self._sleep_backoff(attempt)
                self.logger.warning(
                    "external_request_error_retrying",
                    method=method.upper(),
                    url=url,
                    duration_ms=duration_ms,
                    error=str(exc),
                    next_attempt=attempt + 2,
                )
                continue

            duration_ms = int((time.perf_counter() - start) * 1000)
            self.logger.info(
                "external_request_completed",
                method=method.upper(),
                url=url,
                status_code=response.status_code,
                duration_ms=duration_ms,
            )

            if response.status_code == 429:
                retry_after = self._parse_retry_after(response.headers.get("Retry-After"))
                if attempt >= self.max_retries:
                    self.logger.error(
                        "external_request_rate_limited_exhausted",
                        method=method.upper(),
                        url=url,
                        retry_after=retry_after,
                    )
                    raise SourceFetchError(
                        source=self.source.value,
                        status_code=429,
                        message=f"Rate limited by {self.source.value}",
                    )
                self.logger.warning(
                    "external_request_rate_limited_retrying",
                    method=method.upper(),
                    url=url,
                    retry_after=retry_after,
                    next_attempt=attempt + 2,
                )
                await asyncio.sleep(retry_after)
                continue

            if 500 <= response.status_code < 600:
                if attempt >= self.max_retries:
                    self.logger.error(
                        "external_request_server_error_exhausted",
                        method=method.upper(),
                        url=url,
                        status_code=response.status_code,
                    )
                    raise SourceFetchError(
                        source=self.source.value,
                        status_code=response.status_code,
                    )
                self.logger.warning(
                    "external_request_server_error_retrying",
                    method=method.upper(),
                    url=url,
                    status_code=response.status_code,
                    next_attempt=attempt + 2,
                )
                await self._sleep_backoff(attempt)
                continue

            if response.status_code >= 400:
                self.logger.error(
                    "external_request_client_error",
                    method=method.upper(),
                    url=url,
                    status_code=response.status_code,
                )
                raise SourceFetchError(
                    source=self.source.value,
                    status_code=response.status_code,
                )

            return response

        raise SourceFetchError(source=self.source.value, status_code=500)

    async def _sleep_backoff(self, attempt: int) -> None:
        """Sleep using exponential backoff with a small jitter."""
        jitter = random.uniform(0.0, 0.25)  # nosec: jitter is non-security use
        delay = (2**attempt) + jitter
        await asyncio.sleep(delay)

    async def _apply_local_rate_limit(self) -> None:
        """Throttle outbound calls when a source has a fixed request cadence."""
        if self.min_request_interval <= 0:
            return

        async with self._rate_limit_lock:
            now = time.monotonic()
            elapsed = now - self._last_request_at
            wait_for = self.min_request_interval - elapsed
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            self._last_request_at = time.monotonic()

    def _parse_retry_after(self, header_value: str | None) -> float:
        """Parse Retry-After as seconds, supporting integer or HTTP-date."""
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
            delta = (retry_dt - datetime.now(UTC)).total_seconds()
            return max(0.0, min(delta, max_delay))
        except (TypeError, ValueError):
            return default_delay
