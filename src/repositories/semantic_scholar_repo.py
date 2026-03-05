"""Semantic Scholar repository for enrichment metadata lookups."""

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


class SemanticScholarRepository:
    """Fetch TLDR and citation metadata from Semantic Scholar Graph API."""

    BASE_URL = "https://api.semanticscholar.org/graph/v1"
    FIELDS = "tldr,citationCount,title"
    CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
    REQUEST_TIMEOUT = 30.0
    MAX_RETRIES = 3
    BATCH_SIZE = 500

    def __init__(
        self,
        client: httpx.AsyncClient,
        redis_client: Redis,
        settings: Settings | None = None,
    ) -> None:
        self.client = client
        self.redis_client = redis_client
        self.settings = settings or get_settings()
        self.logger = structlog.get_logger(__name__)

    async def get_paper(self, doi: str | None, pmid: str | None) -> dict[str, Any] | None:
        """Try DOI first then PMID and return the first matching paper."""
        identifiers = self._candidate_identifiers(doi=doi, pmid=pmid)
        if not identifiers:
            return None

        for lookup_id, cache_id in identifiers:
            cache_key = build_cache_key("s2", cache_id)
            cached = await self._cache_get(cache_key)
            if cached is not None:
                return cached

            response = await self._request(
                method="GET",
                url=f"{self.BASE_URL}/paper/{lookup_id}",
                params={"fields": self.FIELDS},
            )
            if response is None:
                continue
            if response.status_code == 404:
                continue
            if response.status_code >= 400:
                self.logger.warning(
                    "semantic_scholar_lookup_failed",
                    status_code=response.status_code,
                    identifier=lookup_id,
                )
                continue

            payload = response.json()
            if isinstance(payload, dict):
                await self._cache_set(cache_key, payload)
                return payload

        return None

    async def batch_get_papers(self, identifiers: list[str]) -> list[dict[str, Any]]:
        """Fetch papers in batches of up to 500 identifiers."""
        if not identifiers:
            return []

        normalized_ids = [value for value in (self._normalize_batch_identifier(item) for item in identifiers) if value]
        if not normalized_ids:
            return []

        papers: list[dict[str, Any]] = []
        for start in range(0, len(normalized_ids), self.BATCH_SIZE):
            batch = normalized_ids[start : start + self.BATCH_SIZE]
            response = await self._request(
                method="POST",
                url=f"{self.BASE_URL}/paper/batch",
                params={"fields": self.FIELDS},
                json={"ids": batch},
            )
            if response is None or response.status_code >= 400:
                continue

            payload = response.json()
            if not isinstance(payload, list):
                continue

            for request_id, item in zip(batch, payload, strict=False):
                if not isinstance(item, dict):
                    continue
                papers.append(item)
                cache_id = self._cache_identifier_from_batch_id(request_id)
                await self._cache_set(build_cache_key("s2", cache_id), item)

        return papers

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response | None:
        """Issue a resilient API request that respects 429 responses."""
        kwargs.setdefault("timeout", self.REQUEST_TIMEOUT)
        kwargs.setdefault("headers", self._headers())

        for attempt in range(self.MAX_RETRIES + 1):
            start = time.perf_counter()
            try:
                response = await self.client.request(method=method, url=url, **kwargs)
            except httpx.HTTPError as exc:
                if attempt >= self.MAX_RETRIES:
                    self.logger.warning("semantic_scholar_request_error", error=str(exc), url=url)
                    return None
                await self._sleep_backoff(attempt)
                continue

            duration_ms = int((time.perf_counter() - start) * 1000)
            self.logger.info(
                "semantic_scholar_request",
                method=method,
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

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.settings.SEMANTIC_SCHOLAR_API_KEY:
            headers["x-api-key"] = self.settings.SEMANTIC_SCHOLAR_API_KEY
        return headers

    def _candidate_identifiers(self, doi: str | None, pmid: str | None) -> list[tuple[str, str]]:
        candidates: list[tuple[str, str]] = []

        normalized_doi = self._normalize_doi(doi)
        if normalized_doi:
            candidates.append((f"DOI:{normalized_doi}", normalized_doi))

        normalized_pmid = self._normalize_pmid(pmid)
        if normalized_pmid:
            candidates.append((f"PMID:{normalized_pmid}", normalized_pmid))

        return candidates

    def _normalize_batch_identifier(self, identifier: str) -> str | None:
        value = (identifier or "").strip()
        if not value:
            return None
        upper = value.upper()
        if upper.startswith("DOI:") or upper.startswith("PMID:"):
            return value
        if value.isdigit():
            return f"PMID:{value}"
        return f"DOI:{self._normalize_doi(value) or value}"

    def _cache_identifier_from_batch_id(self, batch_id: str) -> str:
        if ":" not in batch_id:
            return batch_id
        return batch_id.split(":", 1)[1]

    async def _cache_get(self, key: str) -> dict[str, Any] | None:
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
        return payload if isinstance(payload, dict) else None

    async def _cache_set(self, key: str, value: dict[str, Any]) -> None:
        try:
            await self.redis_client.set(key, json.dumps(value).encode("utf-8"), ex=self.CACHE_TTL_SECONDS)
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

    def _normalize_doi(self, doi: str | None) -> str | None:
        if not doi:
            return None
        value = doi.strip()
        if not value:
            return None
        lowered = value.lower()
        for prefix in ("https://doi.org/", "http://doi.org/", "doi.org/", "doi:"):
            if lowered.startswith(prefix):
                value = value[len(prefix) :]
                break
        return value.strip() or None

    def _normalize_pmid(self, pmid: str | None) -> str | None:
        if not pmid:
            return None
        value = pmid.strip()
        if not value:
            return None
        if value.upper().startswith("PMID:"):
            value = value.split(":", 1)[1]
        return value.strip() or None
