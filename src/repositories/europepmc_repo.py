"""Europe PMC repository implementation."""

from __future__ import annotations

import re
from typing import Any

import httpx

from src.core.exceptions import SourceFetchError
from src.repositories.base_repo import BaseSourceRepository, SortMode
from src.schemas.enums import OAStatus, SourceType
from src.schemas.records import RawRecord


class EuropePMCRepository(BaseSourceRepository):
    """Repository for Europe PMC search API."""

    source = SourceType.EUROPEPMC
    min_request_interval = 0.1  # 10 req/s
    _SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    _FULLTEXT_URL_TEMPLATE = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmid}/fullTextXML"
    _PAGE_SIZE = 100

    async def search(
        self,
        query: str,
        max_results: int = 100,
        sort_mode: SortMode = "relevance",
    ) -> list[RawRecord]:
        """Search Europe PMC via cursor-based pagination.

        ``synonym=true`` enables Europe PMC's MeSH/UMLS synonym expansion which
        materially boosts recall on free-text biomedical queries (matches the
        behavior of europepmc.org's web UI). For BOOLEAN/PRISMA mode we keep
        synonym expansion off and request explicit chronological order so the
        result set is reproducible for a systematic review.
        """
        if not query.strip() or max_results <= 0:
            return []

        cursor = "*"
        records: list[RawRecord] = []

        base_params: dict[str, Any] = {
            "query": query,
            "format": "json",
            "resultType": "core",
            "pageSize": min(self._PAGE_SIZE, max_results),
        }
        if sort_mode == "relevance":
            base_params["synonym"] = "true"
            # Europe PMC defaults to relevance sort when no sort param is given.
        else:
            base_params["sort"] = "P_PDATE_D desc"

        while cursor and len(records) < max_results:
            try:
                response = await self._request(
                    method="GET",
                    url=self._SEARCH_URL,
                    params={**base_params, "cursorMark": cursor},
                )
            except SourceFetchError as exc:
                if records:
                    self.logger.warning(
                        "europepmc_partial_fetch_failed",
                        status_code=exc.status_code,
                        collected_records=len(records),
                    )
                    break
                raise

            payload = response.json()
            result_list = payload.get("resultList", {})
            entries = result_list.get("result", []) if isinstance(result_list, dict) else []

            for entry in entries:
                record = self._entry_to_raw_record(entry)
                if record is not None:
                    records.append(record)
                if len(records) >= max_results:
                    break

            next_cursor = payload.get("nextCursorMark")
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor

        return self._assign_source_ranks(records[:max_results])

    async def fetch_by_id(self, source_id: str) -> RawRecord | None:
        """Fetch a single Europe PMC record by external id."""
        if not source_id.strip():
            return None

        try:
            response = await self._request(
                method="GET",
                url=self._SEARCH_URL,
                params={
                    "query": f"EXT_ID:{source_id}",
                    "format": "json",
                    "resultType": "core",
                    "pageSize": 1,
                    "cursorMark": "*",
                },
            )
        except SourceFetchError:
            return None

        payload = response.json()
        result_list = payload.get("resultList", {})
        entries = result_list.get("result", []) if isinstance(result_list, dict) else []
        if not entries:
            return None
        return self._entry_to_raw_record(entries[0])

    async def get_fulltext_url(self, pmid: str) -> str | None:
        """Return Europe PMC full-text XML URL when full text is available."""
        normalized_pmid = pmid.strip()
        if not normalized_pmid:
            return None

        fulltext_url = self._FULLTEXT_URL_TEMPLATE.format(pmid=normalized_pmid)

        await self._apply_local_rate_limit()
        try:
            response = await self.client.head(fulltext_url, timeout=self.request_timeout)
            if response.status_code == 405:
                response = await self.client.get(fulltext_url, timeout=self.request_timeout)
        except httpx.HTTPError:
            return None

        if response.status_code == 200:
            return fulltext_url
        return None

    def _entry_to_raw_record(self, entry: dict[str, Any]) -> RawRecord | None:
        source_id = (entry.get("pmid") or entry.get("id") or "").strip()
        if not source_id:
            return None

        return RawRecord(
            source_id=source_id,
            source=self.source,
            title=(entry.get("title") or "").strip() or source_id,
            authors=self._split_authors(entry.get("authorString")),
            journal=self._clean_str(entry.get("journalTitle")),
            year=self._parse_year(entry.get("pubYear")),
            doi=self._clean_str(entry.get("doi")),
            pmid=self._clean_str(entry.get("pmid")),
            abstract=self._clean_str(entry.get("abstractText")),
            oa_status=self._parse_oa_status(entry.get("isOpenAccess")),
            raw_data=entry,
        )

    def _split_authors(self, author_string: Any) -> list[str]:
        if not isinstance(author_string, str):
            return []
        return [author.strip() for author in author_string.split(",") if author.strip()]

    def _parse_year(self, value: Any) -> int | None:
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            match = re.search(r"(\d{4})", value)
            if match:
                return int(match.group(1))
        return None

    def _parse_oa_status(self, value: Any) -> OAStatus:
        if isinstance(value, bool):
            return OAStatus.OPEN if value else OAStatus.CLOSED
        if isinstance(value, str):
            normalized = value.strip().upper()
            if normalized in {"Y", "YES", "TRUE", "OPEN"}:
                return OAStatus.OPEN
            if normalized in {"N", "NO", "FALSE", "CLOSED"}:
                return OAStatus.CLOSED
        return OAStatus.UNKNOWN

    def _clean_str(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        cleaned = value.strip()
        return cleaned or None
