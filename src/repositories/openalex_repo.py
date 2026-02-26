"""OpenAlex repository implementation."""

from __future__ import annotations

import re
from typing import Any

from src.core.exceptions import SourceFetchError
from src.repositories.base_repo import BaseSourceRepository
from src.schemas.enums import OAStatus, SourceType
from src.schemas.records import RawRecord


class OpenAlexRepository(BaseSourceRepository):
    """Repository for OpenAlex works API."""

    source = SourceType.OPENALEX
    min_request_interval = 0.1  # polite pool ~10 req/s
    _BASE_URL = "https://api.openalex.org/works"
    _PAGE_SIZE = 50

    async def search(self, query: str, max_results: int = 100) -> list[RawRecord]:
        """Search OpenAlex works using cursor pagination."""
        if not query.strip() or max_results <= 0:
            return []

        cursor = "*"
        records: list[RawRecord] = []

        while cursor and len(records) < max_results:
            try:
                response = await self._request(
                    method="GET",
                    url=self._BASE_URL,
                    params={
                        "search": query,
                        "filter": "is_paratext:false",
                        "mailto": self.settings.CONTACT_EMAIL,
                        "per_page": self._PAGE_SIZE,
                        "cursor": cursor,
                    },
                )
            except SourceFetchError:
                break

            payload = response.json()
            results = payload.get("results", [])
            for work in results:
                record = self._work_to_raw_record(work)
                if record is not None:
                    records.append(record)
                if len(records) >= max_results:
                    break

            cursor = payload.get("meta", {}).get("next_cursor")

        return records[:max_results]

    async def fetch_by_id(self, source_id: str) -> RawRecord | None:
        """Fetch one OpenAlex work by identifier."""
        if not source_id.strip():
            return None

        work_id = self._normalize_work_id(source_id)
        try:
            response = await self._request(
                method="GET",
                url=f"{self._BASE_URL}/{work_id}",
                params={"mailto": self.settings.CONTACT_EMAIL},
            )
        except SourceFetchError:
            return None

        return self._work_to_raw_record(response.json())

    def _work_to_raw_record(self, work: dict[str, Any]) -> RawRecord | None:
        openalex_id = work.get("id")
        if not openalex_id:
            return None

        authorships = work.get("authorships", [])
        authors = [
            authorship.get("author", {}).get("display_name", "").strip()
            for authorship in authorships
            if isinstance(authorship, dict)
        ]
        authors = [author for author in authors if author]

        primary_location = work.get("primary_location") or {}
        source_info = primary_location.get("source") if isinstance(primary_location, dict) else {}
        journal = source_info.get("display_name") if isinstance(source_info, dict) else None

        open_access = work.get("open_access") or {}
        is_oa = bool(open_access.get("is_oa")) if isinstance(open_access, dict) else False
        oa_url = open_access.get("oa_url") if isinstance(open_access, dict) else None

        ids = work.get("ids") or {}
        pmid = self._extract_pmid(ids.get("pmid")) if isinstance(ids, dict) else None

        return RawRecord(
            source_id=str(openalex_id),
            source=self.source,
            title=(work.get("title") or "").strip() or str(openalex_id),
            authors=authors,
            journal=journal,
            year=self._as_int(work.get("publication_year")),
            doi=self._normalize_doi(work.get("doi")),
            pmid=pmid,
            abstract=self._reconstruct_abstract(work.get("abstract_inverted_index")),
            pdf_url=oa_url,
            oa_status=OAStatus.OPEN if is_oa else OAStatus.CLOSED,
            raw_data=work,
        )

    def _reconstruct_abstract(self, inverted_index: Any) -> str | None:
        if not isinstance(inverted_index, dict) or not inverted_index:
            return None

        max_index = -1
        for positions in inverted_index.values():
            if isinstance(positions, list):
                for position in positions:
                    if isinstance(position, int):
                        max_index = max(max_index, position)
        if max_index < 0:
            return None

        words = [""] * (max_index + 1)
        for token, positions in inverted_index.items():
            if not isinstance(token, str) or not isinstance(positions, list):
                continue
            for position in positions:
                if isinstance(position, int) and 0 <= position < len(words):
                    words[position] = token

        abstract = " ".join(word for word in words if word).strip()
        return abstract or None

    def _normalize_work_id(self, source_id: str) -> str:
        value = source_id.strip()
        if value.startswith("https://openalex.org/"):
            return value.rsplit("/", 1)[-1]
        if value.startswith("http://openalex.org/"):
            return value.rsplit("/", 1)[-1]
        return value

    def _normalize_doi(self, doi_value: Any) -> str | None:
        if not isinstance(doi_value, str) or not doi_value.strip():
            return None
        doi = doi_value.strip()
        for prefix in ("https://doi.org/", "http://doi.org/", "doi.org/"):
            if doi.lower().startswith(prefix):
                doi = doi[len(prefix) :]
                break
        return doi or None

    def _extract_pmid(self, pmid_value: Any) -> str | None:
        if not isinstance(pmid_value, str) or not pmid_value.strip():
            return None
        match = re.search(r"(\d+)", pmid_value)
        return match.group(1) if match else None

    def _as_int(self, value: Any) -> int | None:
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return None
