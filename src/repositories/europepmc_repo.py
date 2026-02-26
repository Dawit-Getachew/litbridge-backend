"""Europe PMC repository implementation."""

from __future__ import annotations

import re
from typing import Any

from src.core.exceptions import SourceFetchError
from src.repositories.base_repo import BaseSourceRepository
from src.schemas.enums import OAStatus, SourceType
from src.schemas.records import RawRecord


class EuropePMCRepository(BaseSourceRepository):
    """Repository for Europe PMC search API."""

    source = SourceType.EUROPEPMC
    min_request_interval = 0.1  # 10 req/s
    _SEARCH_URL = "https://europepmc.org/webservices/rest/search"
    _PAGE_SIZE = 100

    async def search(self, query: str, max_results: int = 100) -> list[RawRecord]:
        """Search Europe PMC via cursor-based pagination."""
        if not query.strip() or max_results <= 0:
            return []

        cursor = "*"
        records: list[RawRecord] = []

        while cursor and len(records) < max_results:
            try:
                response = await self._request(
                    method="GET",
                    url=self._SEARCH_URL,
                    params={
                        "query": query,
                        "format": "json",
                        "resultType": "core",
                        "pageSize": min(self._PAGE_SIZE, max_results),
                        "cursorMark": cursor,
                    },
                )
            except SourceFetchError:
                break

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

        return records[:max_results]

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
