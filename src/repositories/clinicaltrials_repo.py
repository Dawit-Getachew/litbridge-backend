"""ClinicalTrials.gov V2 repository implementation."""

from __future__ import annotations

import asyncio
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from src.core.exceptions import SourceFetchError
from src.repositories.base_repo import BaseSourceRepository
from src.schemas.enums import SourceType
from src.schemas.records import RawRecord


class ClinicalTrialsRepository(BaseSourceRepository):
    """Repository for ClinicalTrials.gov V2 studies API."""

    source = SourceType.CLINICALTRIALS
    min_request_interval = 0.2  # conservative ~5 req/s
    _BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
    _PAGE_SIZE = 100
    _FALLBACK_USER_AGENT = "LitBridge/1.0 (+https://litbridge.local)"

    async def search(self, query: str, max_results: int = 100) -> list[RawRecord]:
        """Search studies by query.term with page-token pagination."""
        if not query.strip() or max_results <= 0:
            return []

        page_token: str | None = None
        records: list[RawRecord] = []

        while len(records) < max_results:
            params: dict[str, Any] = {
                "query.term": query,
                "pageSize": min(self._PAGE_SIZE, max_results),
            }
            if page_token:
                params["pageToken"] = page_token

            try:
                payload = await self._request_payload(url=self._BASE_URL, params=params)
            except SourceFetchError as exc:
                if records:
                    self.logger.warning(
                        "clinicaltrials_partial_fetch_failed",
                        status_code=exc.status_code,
                        collected_records=len(records),
                    )
                    break
                raise

            studies = payload.get("studies", [])

            for study in studies:
                record = self._study_to_raw_record(study)
                if record is not None:
                    records.append(record)
                if len(records) >= max_results:
                    break

            page_token = payload.get("nextPageToken")
            if not page_token:
                break

        return records[:max_results]

    async def fetch_by_id(self, source_id: str) -> RawRecord | None:
        """Fetch a single clinical study by NCT id."""
        if not source_id.strip():
            return None

        try:
            payload = await self._request_payload(url=f"{self._BASE_URL}/{source_id}")
        except SourceFetchError:
            return None

        if isinstance(payload, dict) and "studies" in payload:
            studies = payload.get("studies", [])
            if not studies:
                return None
            return self._study_to_raw_record(studies[0])
        if isinstance(payload, dict):
            return self._study_to_raw_record(payload)
        return None

    async def _request_payload(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = await self._request(method="GET", url=url, params=params)
            payload = response.json()
            if isinstance(payload, dict):
                return payload
            raise SourceFetchError(
                source=self.source.value,
                status_code=502,
                message="Invalid JSON payload from ClinicalTrials.gov",
            )
        except SourceFetchError as exc:
            if exc.status_code != 403:
                raise

        fallback_payload = await self._request_payload_via_urllib(url=url, params=params)
        if fallback_payload is not None:
            self.logger.info("clinicaltrials_httpx_403_fallback_succeeded", url=url)
            return fallback_payload

        raise SourceFetchError(
            source=self.source.value,
            status_code=403,
            message="ClinicalTrials.gov rejected request and urllib fallback failed",
        )

    async def _request_payload_via_urllib(
        self,
        url: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        request_url = self._build_url(url=url, params=params)
        headers = {
            "Accept": "application/json",
            "User-Agent": self._FALLBACK_USER_AGENT,
        }

        await self._apply_local_rate_limit()

        def _fetch() -> tuple[int, str]:
            request = urllib.request.Request(request_url, headers=headers, method="GET")
            with urllib.request.urlopen(request, timeout=self.request_timeout) as response:
                status = int(getattr(response, "status", 200))
                body = response.read().decode("utf-8")
            return status, body

        try:
            status_code, raw_body = await asyncio.to_thread(_fetch)
        except urllib.error.HTTPError as exc:
            self.logger.warning(
                "clinicaltrials_urllib_http_error",
                status_code=exc.code,
                url=request_url,
            )
            return None
        except Exception as exc:  # pragma: no cover - defensive external I/O guard
            self.logger.warning(
                "clinicaltrials_urllib_request_error",
                url=request_url,
                error=str(exc),
            )
            return None

        if status_code >= 400:
            self.logger.warning(
                "clinicaltrials_urllib_client_error",
                status_code=status_code,
                url=request_url,
            )
            return None

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError:
            self.logger.warning("clinicaltrials_urllib_decode_failed", url=request_url)
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    def _build_url(self, url: str, params: dict[str, Any] | None = None) -> str:
        if not params:
            return url
        query = urllib.parse.urlencode(params, doseq=True)
        return f"{url}?{query}"

    def _study_to_raw_record(self, study: dict[str, Any]) -> RawRecord | None:
        protocol = study.get("protocolSection", {})
        if not isinstance(protocol, dict):
            return None

        identification = protocol.get("identificationModule", {})
        nct_id = self._clean_str(identification.get("nctId")) if isinstance(identification, dict) else None
        if not nct_id:
            return None

        title = self._clean_str(identification.get("briefTitle")) if isinstance(identification, dict) else None
        contacts = protocol.get("contactsLocationsModule", {})
        conditions_module = protocol.get("conditionsModule", {})
        interventions_module = protocol.get("armsInterventionsModule", {})
        status_module = protocol.get("statusModule", {})
        description_module = protocol.get("descriptionModule", {})

        authors = self._extract_investigators(contacts if isinstance(contacts, dict) else {})
        journal = self._compose_study_description(
            conditions_module if isinstance(conditions_module, dict) else {},
            interventions_module if isinstance(interventions_module, dict) else {},
        )
        year = self._extract_start_year(status_module if isinstance(status_module, dict) else {})
        abstract = self._clean_str(description_module.get("briefSummary")) if isinstance(description_module, dict) else None

        return RawRecord(
            source_id=nct_id,
            source=self.source,
            title=title or nct_id,
            authors=authors,
            journal=journal,
            year=year,
            abstract=abstract,
            raw_data=study,
        )

    def _extract_investigators(self, contacts_module: dict[str, Any]) -> list[str]:
        officials = contacts_module.get("overallOfficials", [])
        if not isinstance(officials, list):
            return []

        authors: list[str] = []
        for official in officials:
            if not isinstance(official, dict):
                continue
            name = self._clean_str(official.get("name"))
            if name:
                authors.append(name)
        return authors

    def _compose_study_description(
        self,
        conditions_module: dict[str, Any],
        interventions_module: dict[str, Any],
    ) -> str | None:
        conditions = conditions_module.get("conditions", [])
        interventions = interventions_module.get("interventions", [])

        condition_names = [value.strip() for value in conditions if isinstance(value, str) and value.strip()]
        intervention_names: list[str] = []
        if isinstance(interventions, list):
            for intervention in interventions:
                if not isinstance(intervention, dict):
                    continue
                name = self._clean_str(intervention.get("name"))
                if name:
                    intervention_names.append(name)

        parts: list[str] = []
        if condition_names:
            parts.append(f"Conditions: {', '.join(condition_names)}")
        if intervention_names:
            parts.append(f"Interventions: {', '.join(intervention_names)}")

        return " | ".join(parts) if parts else None

    def _extract_start_year(self, status_module: dict[str, Any]) -> int | None:
        start_date_struct = status_module.get("startDateStruct", {})
        if not isinstance(start_date_struct, dict):
            return None
        date_value = start_date_struct.get("date")
        if not isinstance(date_value, str):
            return None
        match = re.search(r"(\d{4})", date_value)
        return int(match.group(1)) if match else None

    def _clean_str(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        cleaned = value.strip()
        return cleaned or None
