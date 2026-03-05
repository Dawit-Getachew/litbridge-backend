"""Tests for external source repository implementations."""

from __future__ import annotations

import httpx
import pytest
import respx

from src.repositories import (
    ClinicalTrialsRepository,
    EuropePMCRepository,
    OpenAlexRepository,
    PubMedRepository,
    get_repository,
)
from src.schemas.enums import OAStatus, SourceType

PUBMED_ESEARCH_XML = """<?xml version="1.0" encoding="UTF-8"?>
<eSearchResult>
  <Count>1</Count>
  <RetMax>1</RetMax>
  <RetStart>0</RetStart>
  <IdList>
    <Id>12345</Id>
  </IdList>
  <QueryKey>1</QueryKey>
  <WebEnv>NCID_1_123456789_130.14.22.76_9001_1700000000_1234567890</WebEnv>
</eSearchResult>
"""

PUBMED_EFETCH_XML = """<?xml version="1.0" encoding="UTF-8"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>12345</PMID>
      <Article>
        <ArticleTitle>PubMed Sample Title</ArticleTitle>
        <Abstract>
          <AbstractText>PubMed abstract content.</AbstractText>
        </Abstract>
        <AuthorList>
          <Author>
            <LastName>Doe</LastName>
            <Initials>J</Initials>
          </Author>
          <Author>
            <LastName>Smith</LastName>
            <Initials>A</Initials>
          </Author>
        </AuthorList>
        <Journal>
          <Title>Journal of Testing</Title>
          <JournalIssue>
            <PubDate>
              <Year>2022</Year>
            </PubDate>
          </JournalIssue>
        </Journal>
      </Article>
    </MedlineCitation>
    <PubmedData>
      <ArticleIdList>
        <ArticleId IdType="doi">10.1000/pubmed-doi</ArticleId>
      </ArticleIdList>
    </PubmedData>
  </PubmedArticle>
</PubmedArticleSet>
"""


@pytest.fixture
def no_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable async sleeps so retry tests run quickly."""

    async def _no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("src.repositories.base_repo.asyncio.sleep", _no_sleep)


@pytest.mark.asyncio
async def test_pubmed_repository_maps_esearch_and_efetch_to_raw_record() -> None:
    """PubMed repository should parse XML and map expected fields."""
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi").respond(
            status_code=200,
            text=PUBMED_ESEARCH_XML,
        )
        mock.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi").respond(
            status_code=200,
            text=PUBMED_EFETCH_XML,
        )

        async with httpx.AsyncClient() as client:
            repo = PubMedRepository(client=client)
            records = await repo.search(query="metformin", max_results=5)

    assert len(records) == 1
    record = records[0]
    assert record.source == SourceType.PUBMED
    assert record.source_id == "12345"
    assert record.title == "PubMed Sample Title"
    assert record.authors == ["Doe J", "Smith A"]
    assert record.journal == "Journal of Testing"
    assert record.year == 2022
    assert record.doi == "10.1000/pubmed-doi"
    assert record.pmid == "12345"
    assert record.abstract == "PubMed abstract content."


@pytest.mark.asyncio
async def test_openalex_repository_maps_json_fields_correctly() -> None:
    """OpenAlex repository should map JSON payload to RawRecord."""
    openalex_payload = {
        "results": [
            {
                "id": "https://openalex.org/W123",
                "title": "OpenAlex Sample Title",
                "authorships": [
                    {"author": {"display_name": "Alice Johnson"}},
                    {"author": {"display_name": "Bob Smith"}},
                ],
                "primary_location": {"source": {"display_name": "Open Journal"}},
                "publication_year": 2021,
                "doi": "https://doi.org/10.1000/openalex-doi",
                "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/98765"},
                "abstract_inverted_index": {"OpenAlex": [0], "abstract": [1], "text": [2]},
                "open_access": {"oa_url": "https://example.org/paper.pdf", "is_oa": True},
            }
        ],
        "meta": {"next_cursor": None},
    }

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.openalex.org/works").respond(status_code=200, json=openalex_payload)

        async with httpx.AsyncClient() as client:
            repo = OpenAlexRepository(client=client)
            records = await repo.search(query="oncology", max_results=5)

    assert len(records) == 1
    record = records[0]
    assert record.source == SourceType.OPENALEX
    assert record.source_id == "https://openalex.org/W123"
    assert record.title == "OpenAlex Sample Title"
    assert record.authors == ["Alice Johnson", "Bob Smith"]
    assert record.journal == "Open Journal"
    assert record.year == 2021
    assert record.doi == "10.1000/openalex-doi"
    assert record.pmid == "98765"
    assert record.abstract == "OpenAlex abstract text"
    assert record.pdf_url == "https://example.org/paper.pdf"
    assert record.oa_status == OAStatus.OPEN


@pytest.mark.asyncio
async def test_europepmc_repository_maps_json_fields_correctly() -> None:
    """Europe PMC repository should map expected fields from result list."""
    europepmc_payload = {
        "resultList": {
            "result": [
                {
                    "id": "MED-1",
                    "title": "Europe PMC Title",
                    "authorString": "Miller J, Adams P",
                    "journalTitle": "Europe Journal",
                    "pubYear": "2020",
                    "doi": "10.1000/europepmc-doi",
                    "pmid": "556677",
                    "abstractText": "Europe PMC abstract.",
                    "isOpenAccess": "Y",
                }
            ]
        },
        "nextCursorMark": "*",
    }

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search").respond(
            status_code=200,
            json=europepmc_payload,
        )

        async with httpx.AsyncClient() as client:
            repo = EuropePMCRepository(client=client)
            records = await repo.search(query="asthma", max_results=5)

    assert len(records) == 1
    record = records[0]
    assert record.source == SourceType.EUROPEPMC
    assert record.source_id == "556677"
    assert record.title == "Europe PMC Title"
    assert record.authors == ["Miller J", "Adams P"]
    assert record.journal == "Europe Journal"
    assert record.year == 2020
    assert record.doi == "10.1000/europepmc-doi"
    assert record.pmid == "556677"
    assert record.abstract == "Europe PMC abstract."
    assert record.oa_status == OAStatus.OPEN


@pytest.mark.asyncio
async def test_clinicaltrials_repository_maps_json_fields_correctly() -> None:
    """ClinicalTrials repository should map nested protocol fields."""
    studies_payload = {
        "studies": [
            {
                "protocolSection": {
                    "identificationModule": {
                        "nctId": "NCT01234567",
                        "briefTitle": "Metformin Trial",
                    },
                    "contactsLocationsModule": {
                        "overallOfficials": [{"name": "Dr Jane Doe"}, {"name": "Dr Alex Smith"}]
                    },
                    "conditionsModule": {"conditions": ["Type 2 Diabetes"]},
                    "armsInterventionsModule": {"interventions": [{"name": "Metformin"}]},
                    "statusModule": {"startDateStruct": {"date": "2019-01-15"}},
                    "descriptionModule": {"briefSummary": "A randomized trial."},
                }
            }
        ]
    }

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://clinicaltrials.gov/api/v2/studies").respond(status_code=200, json=studies_payload)

        async with httpx.AsyncClient() as client:
            repo = ClinicalTrialsRepository(client=client)
            records = await repo.search(query="metformin diabetes", max_results=5)

    assert len(records) == 1
    record = records[0]
    assert record.source == SourceType.CLINICALTRIALS
    assert record.source_id == "NCT01234567"
    assert record.title == "Metformin Trial"
    assert record.authors == ["Dr Jane Doe", "Dr Alex Smith"]
    assert record.journal == "Conditions: Type 2 Diabetes | Interventions: Metformin"
    assert record.year == 2019
    assert record.abstract == "A randomized trial."
    assert record.doi is None
    assert record.pmid is None


@pytest.mark.asyncio
async def test_rate_limit_429_retries_and_succeeds(no_retry_sleep: None) -> None:
    """A 429 response should retry and eventually succeed."""
    successful_payload = {"results": [], "meta": {"next_cursor": None}}

    with respx.mock(assert_all_called=True) as mock:
        route = mock.get("https://api.openalex.org/works")
        route.side_effect = [
            httpx.Response(status_code=429, headers={"Retry-After": "0"}),
            httpx.Response(status_code=200, json=successful_payload),
        ]

        async with httpx.AsyncClient() as client:
            repo = OpenAlexRepository(client=client)
            records = await repo.search(query="rate limit", max_results=5)

    assert records == []
    assert route.call_count == 2


@pytest.mark.asyncio
async def test_timeout_handling_returns_empty_list(no_retry_sleep: None) -> None:
    """Timeout failures should fail gracefully and return no records."""
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.openalex.org/works").mock(side_effect=httpx.ReadTimeout("timed out"))

        async with httpx.AsyncClient() as client:
            repo = OpenAlexRepository(client=client)
            records = await repo.search(query="timeout case", max_results=5)

    assert records == []


@pytest.mark.asyncio
async def test_partial_failure_returns_records_collected_before_failure(no_retry_sleep: None) -> None:
    """If a later page fails, already collected records should still be returned."""
    first_page_payload = {
        "results": [
            {
                "id": "https://openalex.org/W555",
                "title": "First page record",
                "authorships": [],
                "primary_location": {"source": {"display_name": "Open Journal"}},
                "publication_year": 2024,
                "doi": None,
                "ids": {},
                "abstract_inverted_index": {},
                "open_access": {"oa_url": None, "is_oa": False},
            }
        ],
        "meta": {"next_cursor": "next-page"},
    }
    with respx.mock(assert_all_called=True) as mock:
        second_page_calls = {"count": 0}

        def openalex_callback(request: httpx.Request) -> httpx.Response:
            cursor = request.url.params.get("cursor")
            if cursor == "*":
                return httpx.Response(status_code=200, json=first_page_payload)
            second_page_calls["count"] += 1
            return httpx.Response(status_code=500, json={"error": "boom"})

        mock.get("https://api.openalex.org/works").mock(side_effect=openalex_callback)

        async with httpx.AsyncClient() as client:
            repo = OpenAlexRepository(client=client)
            records = await repo.search(query="partial fail", max_results=100)

    assert len(records) == 1
    assert records[0].source_id == "https://openalex.org/W555"
    assert second_page_calls["count"] >= 1


@pytest.mark.asyncio
async def test_pubmed_fetch_by_id_returns_record() -> None:
    """PubMed fetch_by_id should parse one record from efetch."""
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi").respond(
            status_code=200,
            text=PUBMED_EFETCH_XML,
        )

        async with httpx.AsyncClient() as client:
            repo = PubMedRepository(client=client)
            record = await repo.fetch_by_id("12345")

    assert record is not None
    assert record.source_id == "12345"
    assert record.title == "PubMed Sample Title"


@pytest.mark.asyncio
async def test_repository_factory_returns_expected_concrete_repositories() -> None:
    """Factory should map each SourceType to the correct repository class."""
    async with httpx.AsyncClient() as client:
        assert isinstance(get_repository(SourceType.PUBMED, client), PubMedRepository)
        assert isinstance(get_repository(SourceType.OPENALEX, client), OpenAlexRepository)
        assert isinstance(get_repository(SourceType.EUROPEPMC, client), EuropePMCRepository)
        assert isinstance(get_repository(SourceType.CLINICALTRIALS, client), ClinicalTrialsRepository)
