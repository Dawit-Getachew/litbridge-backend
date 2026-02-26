"""PubMed repository implementation using NCBI E-Utilities."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from src.core.exceptions import SourceFetchError
from src.repositories.base_repo import BaseSourceRepository
from src.schemas.enums import SourceType
from src.schemas.records import RawRecord


class PubMedRepository(BaseSourceRepository):
    """Repository for PubMed E-Utilities integration."""

    source = SourceType.PUBMED
    min_request_interval = 0.1  # 10 req/s with API key
    _BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    _BATCH_SIZE = 250

    async def search(self, query: str, max_results: int = 100) -> list[RawRecord]:
        """Search PubMed via esearch then fetch article details with efetch."""
        if not query.strip() or max_results <= 0:
            return []

        try:
            esearch_response = await self._request(
                method="GET",
                url=f"{self._BASE_URL}/esearch.fcgi",
                params={
                    "db": "pubmed",
                    "term": query,
                    "retmax": max_results,
                    "usehistory": "y",
                    "retmode": "xml",
                    "api_key": self.settings.NCBI_API_KEY,
                    "email": self.settings.CONTACT_EMAIL,
                },
            )
            pmids, count, webenv, query_key = self._parse_esearch(esearch_response.text)
        except SourceFetchError:
            return []

        if not pmids:
            return []

        target_count = min(max_results, count or len(pmids))
        records: list[RawRecord] = []

        if webenv and query_key:
            for retstart in range(0, target_count, self._BATCH_SIZE):
                retmax = min(self._BATCH_SIZE, target_count - retstart)
                try:
                    efetch_response = await self._request(
                        method="GET",
                        url=f"{self._BASE_URL}/efetch.fcgi",
                        params={
                            "db": "pubmed",
                            "query_key": query_key,
                            "WebEnv": webenv,
                            "retstart": retstart,
                            "retmax": retmax,
                            "retmode": "xml",
                            "api_key": self.settings.NCBI_API_KEY,
                            "email": self.settings.CONTACT_EMAIL,
                        },
                    )
                    records.extend(self._parse_efetch_records(efetch_response.text))
                except SourceFetchError:
                    # Partial failure is acceptable for federated fetch.
                    break
                if len(records) >= target_count:
                    break
            return records[:target_count]

        # Fallback path when usehistory is unavailable.
        selected_pmids = pmids[:target_count]
        for index in range(0, len(selected_pmids), self._BATCH_SIZE):
            chunk = selected_pmids[index : index + self._BATCH_SIZE]
            try:
                efetch_response = await self._request(
                    method="GET",
                    url=f"{self._BASE_URL}/efetch.fcgi",
                    params={
                        "db": "pubmed",
                        "id": ",".join(chunk),
                        "retmode": "xml",
                        "api_key": self.settings.NCBI_API_KEY,
                        "email": self.settings.CONTACT_EMAIL,
                    },
                )
                records.extend(self._parse_efetch_records(efetch_response.text))
            except SourceFetchError:
                break

        return records[:target_count]

    async def fetch_by_id(self, source_id: str) -> RawRecord | None:
        """Fetch a single PubMed article by PMID."""
        if not source_id.strip():
            return None

        try:
            response = await self._request(
                method="GET",
                url=f"{self._BASE_URL}/efetch.fcgi",
                params={
                    "db": "pubmed",
                    "id": source_id,
                    "retmode": "xml",
                    "api_key": self.settings.NCBI_API_KEY,
                    "email": self.settings.CONTACT_EMAIL,
                },
            )
            records = self._parse_efetch_records(response.text)
            return records[0] if records else None
        except SourceFetchError:
            return None

    def _parse_esearch(self, xml_payload: str) -> tuple[list[str], int, str | None, str | None]:
        """Parse esearch XML to PMID list and history metadata."""
        try:
            root = ET.fromstring(xml_payload)
        except ET.ParseError as exc:
            raise SourceFetchError(
                source=self.source.value,
                status_code=502,
                message="Invalid XML payload from PubMed esearch",
            ) from exc

        pmids = [node.text.strip() for node in root.findall("./IdList/Id") if node.text and node.text.strip()]
        count_text = root.findtext("./Count") or "0"
        try:
            count = int(count_text)
        except ValueError:
            count = 0

        webenv = root.findtext("./WebEnv")
        query_key = root.findtext("./QueryKey")
        return pmids, count, webenv, query_key

    def _parse_efetch_records(self, xml_payload: str) -> list[RawRecord]:
        """Parse efetch XML into normalized RawRecord instances."""
        try:
            root = ET.fromstring(xml_payload)
        except ET.ParseError as exc:
            raise SourceFetchError(
                source=self.source.value,
                status_code=502,
                message="Invalid XML payload from PubMed efetch",
            ) from exc

        records: list[RawRecord] = []
        for article in root.findall(".//PubmedArticle"):
            record = self._article_to_raw_record(article)
            if record is not None:
                records.append(record)
        return records

    def _article_to_raw_record(self, article: ET.Element) -> RawRecord | None:
        """Convert one PubMed article XML block to RawRecord."""
        pmid = self._first_text(article, "./MedlineCitation/PMID")
        if not pmid:
            return None

        title = self._first_text(article, "./MedlineCitation/Article/ArticleTitle") or f"PMID {pmid}"
        journal = self._first_text(article, "./MedlineCitation/Article/Journal/Title")
        year = self._parse_year(
            self._first_text(article, "./MedlineCitation/Article/Journal/JournalIssue/PubDate/Year")
            or self._first_text(article, "./MedlineCitation/Article/Journal/JournalIssue/PubDate/MedlineDate")
        )
        doi = self._first_text(article, "./PubmedData/ArticleIdList/ArticleId[@IdType='doi']")
        abstract_nodes = article.findall("./MedlineCitation/Article/Abstract/AbstractText")
        abstract_parts = [self._element_text(node) for node in abstract_nodes]
        abstract = "\n".join(part for part in abstract_parts if part)

        authors: list[str] = []
        for author in article.findall("./MedlineCitation/Article/AuthorList/Author"):
            collective = self._first_text(author, "./CollectiveName")
            if collective:
                authors.append(collective)
                continue
            last_name = self._first_text(author, "./LastName")
            initials = self._first_text(author, "./Initials")
            fore_name = self._first_text(author, "./ForeName")
            if last_name and initials:
                authors.append(f"{last_name} {initials}")
            elif fore_name and last_name:
                authors.append(f"{fore_name} {last_name}")
            elif last_name:
                authors.append(last_name)

        return RawRecord(
            source_id=pmid,
            source=self.source,
            title=title,
            authors=authors,
            journal=journal,
            year=year,
            doi=doi,
            pmid=pmid,
            abstract=abstract or None,
            raw_data={"pmid": pmid},
        )

    def _first_text(self, parent: ET.Element, xpath: str) -> str | None:
        node = parent.find(xpath)
        if node is None:
            return None
        value = self._element_text(node)
        return value or None

    def _element_text(self, node: ET.Element) -> str:
        return "".join(node.itertext()).strip()

    def _parse_year(self, value: str | None) -> int | None:
        if not value:
            return None
        match = re.search(r"(\d{4})", value)
        if not match:
            return None
        return int(match.group(1))
