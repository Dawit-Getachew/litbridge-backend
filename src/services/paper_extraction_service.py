"""AI-powered paper metadata extraction for table view display."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from uuid import UUID

import structlog
from redis import RedisError
from redis.asyncio import Redis

from src.ai.llm_client import LLMClient
from src.core.redis import build_cache_key
from src.repositories.research_collection_repo import ResearchCollectionRepository
from src.repositories.search_repo import SearchRepository
from src.schemas.research_collection import PaperMetadata
from src.workflow.prompts import build_paper_metadata_messages

logger = structlog.get_logger(__name__)

_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", re.DOTALL)
_INNER_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)
_CACHE_TTL = 30 * 24 * 60 * 60
_BATCH_CONCURRENCY = 5
_DEFAULT_METADATA = PaperMetadata()

_VALID_KEYS = {f.strip() for f in PaperMetadata.model_fields}


def _strip_fences(text: str) -> str:
    text = text.strip()
    m = _FENCE_RE.match(text)
    if m:
        return m.group(1).strip()
    m = _INNER_FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    return text


def _normalize_keys(data: dict[str, Any]) -> dict[str, Any]:
    """Convert keys like 'Study Details' or 'studyDesign' to snake_case."""
    normalized: dict[str, Any] = {}
    for key, value in data.items():
        snake = re.sub(r"[\s\-]+", "_", key).lower()
        snake = re.sub(r"[^a-z0-9_]", "", snake)
        if snake in _VALID_KEYS:
            normalized[snake] = value
    return normalized


class PaperExtractionService:
    """Extract structured paper metadata via LLM with Redis caching and DB persistence."""

    def __init__(
        self,
        llm_client: LLMClient,
        redis_client: Redis,
        repo: ResearchCollectionRepository,
        search_repo: SearchRepository | None = None,
    ) -> None:
        self.llm = llm_client
        self.redis = redis_client
        self.repo = repo
        self.search_repo = search_repo

    async def extract_metadata(
        self,
        title: str,
        abstract: str | None,
        record_id: str,
    ) -> PaperMetadata:
        """Extract structured metadata from a paper title and abstract.

        Checks Redis cache first, then calls LLM if needed. Result is
        cached in Redis with 30-day TTL.
        """
        cache_key = build_cache_key("paper_meta", record_id)
        cached = await self._cache_get(cache_key)
        if cached is not None:
            return cached

        if not abstract or not abstract.strip():
            return _DEFAULT_METADATA

        messages = build_paper_metadata_messages(title, abstract)
        payload = {
            "model": self.llm.model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 1000,
            "response_format": {"type": "json_object"},
        }

        try:
            response = await self.llm.client.post(
                f"{self.llm.base_url}/chat/completions",
                json=payload,
                headers=self.llm._headers(),
                timeout=30.0,
            )
            if response.status_code >= 400:
                logger.warning("paper_extraction_failed", status=response.status_code)
                return _DEFAULT_METADATA

            content = self.llm._extract_message_content(response.json())
            if not content:
                return _DEFAULT_METADATA

            data = json.loads(_strip_fences(content))
            if isinstance(data, dict):
                data = _normalize_keys(data)
            metadata = PaperMetadata.model_validate(data)
        except Exception as exc:
            logger.warning("paper_extraction_error", record_id=record_id, error=str(exc))
            return _DEFAULT_METADATA

        await self._cache_set(cache_key, metadata)
        return metadata

    async def extract_and_persist(
        self,
        item_id: UUID,
        title: str,
        abstract: str | None,
        record_id: str,
        search_session_id: UUID | None = None,
    ) -> PaperMetadata:
        """Extract metadata and persist to the database item.

        If abstract is None, attempts to resolve it from the search
        session's stored results using search_session_id + record_id.
        """
        if not abstract and search_session_id and self.search_repo:
            abstract = await self._resolve_abstract(record_id, search_session_id)

        metadata = await self.extract_metadata(title, abstract, record_id)
        try:
            await self.repo.update_item_metadata(
                item_id, metadata.model_dump(),
            )
        except Exception as exc:
            logger.warning(
                "paper_metadata_persist_failed",
                item_id=str(item_id),
                error=str(exc),
            )
        return metadata

    async def extract_batch(
        self,
        items: list[dict[str, Any]],
    ) -> list[PaperMetadata]:
        """Extract metadata for multiple papers with bounded concurrency.

        Each item dict should have: item_id, title, record_id, search_session_id.
        abstract is optional -- resolved from search results if missing.
        """
        if not items:
            return []

        semaphore = asyncio.Semaphore(_BATCH_CONCURRENCY)

        async def _extract_one(item: dict[str, Any]) -> PaperMetadata:
            async with semaphore:
                return await self.extract_and_persist(
                    item_id=item["item_id"],
                    title=item["title"],
                    abstract=item.get("abstract"),
                    record_id=item["record_id"],
                    search_session_id=item.get("search_session_id"),
                )

        return list(await asyncio.gather(*(_extract_one(it) for it in items)))

    async def _resolve_abstract(
        self, record_id: str, search_session_id: UUID,
    ) -> str | None:
        """Look up a record's abstract from the search session's stored results."""
        try:
            session = await self.search_repo.get_session(str(search_session_id))
            if session is None or not session.results:
                return None
            for result in session.results:
                if isinstance(result, dict) and result.get("id") == record_id:
                    return result.get("abstract")
        except Exception as exc:
            logger.warning(
                "abstract_resolve_failed",
                record_id=record_id,
                session_id=str(search_session_id),
                error=str(exc),
            )
        return None

    async def _cache_get(self, key: str) -> PaperMetadata | None:
        try:
            cached = await self.redis.get(key)
        except RedisError:
            return None
        if not cached:
            return None
        try:
            data = json.loads(cached.decode("utf-8"))
            return PaperMetadata.model_validate(data)
        except Exception:
            return None

    async def _cache_set(self, key: str, value: PaperMetadata) -> None:
        try:
            payload = value.model_dump(mode="json")
            await self.redis.set(
                key,
                json.dumps(payload).encode("utf-8"),
                ex=_CACHE_TTL,
            )
        except (RedisError, TypeError, ValueError):
            pass
