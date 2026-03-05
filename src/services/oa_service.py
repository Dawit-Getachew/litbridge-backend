"""Open-access resolution service using OpenAlex, Unpaywall, and Europe PMC."""

from __future__ import annotations

import asyncio
import json

import structlog
from redis import RedisError
from redis.asyncio import Redis

from src.core.redis import build_cache_key
from src.repositories.europepmc_repo import EuropePMCRepository
from src.repositories.openalex_repo import OpenAlexRepository
from src.repositories.unpaywall_repo import UnpaywallRepository
from src.schemas.enums import OAStatus
from src.schemas.records import UnifiedRecord


class OAService:
    """Resolve open-access full-text links through a first-hit cascade."""

    OPEN_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
    CLOSED_CACHE_TTL_SECONDS = 24 * 60 * 60
    BATCH_CONCURRENCY = 10

    def __init__(
        self,
        openalex_repo: OpenAlexRepository,
        unpaywall_repo: UnpaywallRepository,
        europepmc_repo: EuropePMCRepository,
        redis_client: Redis,
    ) -> None:
        # Kept for source parity in the OA cascade; OpenAlex data is expected on the record.
        self.openalex_repo = openalex_repo
        self.unpaywall_repo = unpaywall_repo
        self.europepmc_repo = europepmc_repo
        self.redis_client = redis_client
        self.logger = structlog.get_logger(__name__).bind(service="oa_service")

    async def resolve_oa(self, record: UnifiedRecord) -> tuple[OAStatus, str | None]:
        """Resolve OA status and full-text URL for a single unified record."""
        cache_key = self._build_cache_key(record)
        if cache_key is not None:
            cached = await self._cache_get(cache_key)
            if cached is not None:
                return cached

        existing_pdf = self._clean_url(record.pdf_url)
        if existing_pdf and record.oa_status is OAStatus.OPEN:
            result = (OAStatus.OPEN, existing_pdf)
            await self._cache_set(cache_key, *result)
            return result

        if record.doi:
            openalex_result = self._resolve_from_openalex_fields(record)
            if openalex_result is not None:
                await self._cache_set(cache_key, *openalex_result)
                return openalex_result

            try:
                unpaywall_status, unpaywall_pdf = await self.unpaywall_repo.get_oa_url(record.doi)
            except Exception:
                unpaywall_status, unpaywall_pdf = OAStatus.UNKNOWN, None

            cleaned_unpaywall_pdf = self._clean_url(unpaywall_pdf)
            if cleaned_unpaywall_pdf:
                result = (OAStatus.OPEN if unpaywall_status is OAStatus.UNKNOWN else unpaywall_status, cleaned_unpaywall_pdf)
                await self._cache_set(cache_key, *result)
                return result

        if record.pmid:
            try:
                europepmc_url = await self.europepmc_repo.get_fulltext_url(record.pmid)
            except Exception:
                europepmc_url = None
            cleaned_europepmc_url = self._clean_url(europepmc_url)
            if cleaned_europepmc_url:
                result = (OAStatus.OPEN, cleaned_europepmc_url)
                await self._cache_set(cache_key, *result)
                return result

        if not record.doi and not record.pmid:
            return OAStatus.UNKNOWN, None

        closed_result = (OAStatus.CLOSED, None)
        await self._cache_set(cache_key, *closed_result)
        return closed_result

    async def resolve_batch(self, records: list[UnifiedRecord]) -> dict[str, tuple[OAStatus, str | None]]:
        """Resolve OA results concurrently for multiple records."""
        if not records:
            return {}

        semaphore = asyncio.Semaphore(self.BATCH_CONCURRENCY)

        async def resolve_with_limit(record: UnifiedRecord) -> tuple[str, tuple[OAStatus, str | None]]:
            async with semaphore:
                return record.id, await self.resolve_oa(record)

        resolved = await asyncio.gather(*(resolve_with_limit(record) for record in records))
        return {record_id: result for record_id, result in resolved}

    def _resolve_from_openalex_fields(self, record: UnifiedRecord) -> tuple[OAStatus, str] | None:
        pdf_url = self._clean_url(record.pdf_url)
        if not pdf_url:
            return None
        if record.oa_status is not OAStatus.OPEN:
            return None
        return OAStatus.OPEN, pdf_url

    def _build_cache_key(self, record: UnifiedRecord) -> str | None:
        identifier = self._normalize_doi(record.doi) or self._normalize_pmid(record.pmid)
        if identifier is None:
            return None
        return build_cache_key("oa", identifier)

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
            status = OAStatus(status_value)
        except ValueError:
            return None

        pdf_url = payload.get("pdf_url")
        if isinstance(pdf_url, str):
            pdf_url = pdf_url.strip() or None
        else:
            pdf_url = None
        return status, pdf_url

    async def _cache_set(self, key: str | None, oa_status: OAStatus, pdf_url: str | None) -> None:
        if key is None:
            return

        ttl = self.OPEN_CACHE_TTL_SECONDS if oa_status is OAStatus.OPEN else self.CLOSED_CACHE_TTL_SECONDS
        payload = {"oa_status": oa_status.value, "pdf_url": pdf_url}
        try:
            await self.redis_client.set(
                key,
                json.dumps(payload).encode("utf-8"),
                ex=ttl,
            )
        except (RedisError, TypeError, ValueError):
            return None

    def _clean_url(self, value: str | None) -> str | None:
        if not isinstance(value, str):
            return None
        cleaned = value.strip()
        return cleaned or None

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
        normalized = value.strip()
        return normalized or None

    def _normalize_pmid(self, pmid: str | None) -> str | None:
        if not pmid:
            return None
        value = pmid.strip()
        if not value:
            return None
        if value.upper().startswith("PMID:"):
            value = value.split(":", 1)[1]
        normalized = value.strip()
        return normalized or None
