"""Shared fixtures for integration-style API tests."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
import respx
from httpx import ASGITransport, AsyncClient, Response

from src.ai.llm_client import LLMClient
from src.core import deps
from src.core.config import Settings, get_settings
from src.repositories.europepmc_repo import EuropePMCRepository
from src.repositories.openalex_repo import OpenAlexRepository
from src.repositories.search_repo import SearchRepository
from src.repositories.semantic_scholar_repo import SemanticScholarRepository
from src.repositories.unpaywall_repo import UnpaywallRepository
from src.schemas.enums import OAStatus, QueryType, SearchMode, SourceType
from src.schemas.records import UnifiedRecord
from src.schemas.search import SearchRequest
from src.services.dedup_service import DedupService
from src.services.enrichment_service import EnrichmentService
from src.services.fetcher_service import FetcherService
from src.services.oa_service import OAService
from src.services.prisma_service import PrismaService
from src.services.search_service import SearchService
from src.services.streaming_search_service import StreamingSearchService


@dataclass
class FakeSearchSession:
    """In-memory search session model used for integration testing."""

    id: UUID
    query: str
    query_type: str
    search_mode: str
    sources: list[str]
    pico: dict | None
    status: str = "processing"
    total_identified: int = 0
    total_after_dedup: int = 0
    results: list[dict] = field(default_factory=list)
    sources_completed: list[str] = field(default_factory=list)
    sources_failed: list[str] = field(default_factory=list)
    completed_at: datetime | None = None


class InMemorySearchRepository:
    """Search repository substitute with cursor pagination support."""

    def __init__(self) -> None:
        self.sessions: dict[str, FakeSearchSession] = {}

    async def create_session(self, request: SearchRequest, *, user_id: object = None) -> FakeSearchSession:
        session = FakeSearchSession(
            id=uuid4(),
            query=request.query,
            query_type=request.query_type.value,
            search_mode=request.search_mode.value,
            sources=[source.value for source in (request.sources or [])],
            pico=request.pico.model_dump(mode="json") if request.pico else None,
        )
        self.sessions[str(session.id)] = session
        return session

    async def update_session(self, session: FakeSearchSession) -> None:
        self.sessions[str(session.id)] = session

    async def get_session(self, search_id: str) -> FakeSearchSession | None:
        try:
            _ = UUID(search_id)
        except (TypeError, ValueError):
            return None
        return self.sessions.get(search_id)

    async def store_results(self, search_id: str, records: list[UnifiedRecord]) -> None:
        session = await self.get_session(search_id)
        if session is None:
            return
        session.results = [record.model_dump(mode="json") for record in records]
        session.total_after_dedup = len(records)

    async def get_results_page(
        self,
        search_id: str,
        cursor: str | None,
        page_size: int = 20,
    ) -> tuple[list[UnifiedRecord], str | None]:
        session = await self.get_session(search_id)
        if session is None:
            return [], None

        offset = self._decode_cursor(cursor)
        page_data = session.results[offset : offset + page_size]
        records = [UnifiedRecord.model_validate(item) for item in page_data]
        next_offset = offset + len(records)
        next_cursor = self._encode_cursor(next_offset) if next_offset < len(session.results) else None
        return records, next_cursor

    @staticmethod
    def _encode_cursor(offset: int) -> str:
        return base64.urlsafe_b64encode(str(offset).encode("utf-8")).decode("utf-8")

    @staticmethod
    def _decode_cursor(cursor: str | None) -> int:
        if cursor is None:
            return 0
        try:
            return int(base64.urlsafe_b64decode(cursor.encode("utf-8")).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return 0


class InMemoryRedis:
    """Simple in-memory async Redis substitute."""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}
        self.get_calls = 0
        self.set_calls = 0

    async def get(self, key: str) -> bytes | None:
        self.get_calls += 1
        return self._store.get(key)

    async def set(self, key: str, value: bytes | str, ex: int | None = None) -> bool:  # noqa: ARG002
        self.set_calls += 1
        self._store[key] = value if isinstance(value, bytes) else value.encode("utf-8")
        return True

    async def delete(self, *keys: str) -> int:
        deleted = 0
        for key in keys:
            if key in self._store:
                deleted += 1
                del self._store[key]
        return deleted

    async def ping(self) -> bool:
        return True

    async def flushdb(self) -> bool:
        self._store.clear()
        return True


class FakeLLMClient:
    """Deterministic LLM stub for enrichment and streaming tests."""

    async def generate_tldr(self, title: str, abstract: str) -> str | None:
        if not abstract.strip():
            return None
        return f"TLDR: {title[:48]}"

    async def stream_analysis(self, query: str, records: list[UnifiedRecord]):
        yield f"Thinking about {query} across {len(records)} records."
        yield "Most studies suggest a beneficial signal."

    async def quick_summary(self, query: str, records: list[UnifiedRecord]) -> str | None:
        return f"Quick summary for {query} ({len(records)} records)."


@dataclass
class IntegrationContext:
    """Container for shared integration test dependencies."""

    client: AsyncClient
    settings: Settings
    search_repo: InMemorySearchRepository
    redis_client: InMemoryRedis
    http_client: httpx.AsyncClient
    search_service: SearchService
    streaming_search_service: StreamingSearchService


def _build_pubmed_esearch_xml(pmids: list[str]) -> str:
    ids = "\n".join(f"    <Id>{pmid}</Id>" for pmid in pmids)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<eSearchResult>\n"
        f"  <Count>{len(pmids)}</Count>\n"
        f"  <RetMax>{len(pmids)}</RetMax>\n"
        "  <RetStart>0</RetStart>\n"
        "  <IdList>\n"
        f"{ids}\n"
        "  </IdList>\n"
        "  <QueryKey>1</QueryKey>\n"
        "  <WebEnv>NCID_1_test</WebEnv>\n"
        "</eSearchResult>\n"
    )


def _build_pubmed_efetch_xml(records: list[dict[str, Any]]) -> str:
    articles = []
    for record in records:
        article = (
            "  <PubmedArticle>\n"
            "    <MedlineCitation>\n"
            f"      <PMID>{record['pmid']}</PMID>\n"
            "      <Article>\n"
            f"        <ArticleTitle>{record['title']}</ArticleTitle>\n"
            "        <Abstract>\n"
            f"          <AbstractText>{record['abstract']}</AbstractText>\n"
            "        </Abstract>\n"
            "        <AuthorList>\n"
            f"          <Author><LastName>{record['author_last']}</LastName><Initials>{record['author_initial']}</Initials></Author>\n"
            "        </AuthorList>\n"
            "        <Journal>\n"
            "          <Title>Journal of Integration Tests</Title>\n"
            "          <JournalIssue>\n"
            "            <PubDate>\n"
            f"              <Year>{record['year']}</Year>\n"
            "            </PubDate>\n"
            "          </JournalIssue>\n"
            "        </Journal>\n"
            "      </Article>\n"
            "    </MedlineCitation>\n"
            "    <PubmedData>\n"
            "      <ArticleIdList>\n"
            f"        <ArticleId IdType=\"doi\">{record['doi']}</ArticleId>\n"
            "      </ArticleIdList>\n"
            "    </PubmedData>\n"
            "  </PubmedArticle>"
        )
        articles.append(article)

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<PubmedArticleSet>\n"
        f"{'\n'.join(articles)}\n"
        "</PubmedArticleSet>\n"
    )


@pytest.fixture(autouse=True)
def disable_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable retry backoff sleeps so integration tests stay fast."""

    async def _no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("src.repositories.base_repo.asyncio.sleep", _no_sleep)
    monkeypatch.setattr("src.repositories.semantic_scholar_repo.asyncio.sleep", _no_sleep)
    monkeypatch.setattr("src.repositories.unpaywall_repo.asyncio.sleep", _no_sleep)


@pytest.fixture
def canned_responses() -> dict[str, Any]:
    """Realistic canned API responses used across integration tests."""
    pubmed_records: list[dict[str, Any]] = []
    for idx in range(1, 25):
        doi = f"10.5555/pubmed-{idx}"
        if idx == 1:
            doi = "10.5555/shared-1"
        elif idx == 2:
            doi = "10.5555/shared-2"
        pubmed_records.append(
            {
                "pmid": str(900000 + idx),
                "title": f"Metformin cardiovascular trial {idx}",
                "abstract": f"Study {idx} investigates metformin and cardiovascular outcomes.",
                "author_last": f"Author{idx}",
                "author_initial": "A",
                "year": 2016 + (idx % 9),
                "doi": doi,
            }
        )

    openalex_results = [
        {
            "id": "https://openalex.org/W1001",
            "title": "OpenAlex overlap study 1",
            "authorships": [{"author": {"display_name": "Open Author One"}}],
            "primary_location": {"source": {"display_name": "OpenAlex Journal"}},
            "publication_year": 2021,
            "doi": "https://doi.org/10.5555/shared-1",
            "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/900001"},
            "abstract_inverted_index": {"OpenAlex": [0], "overlap": [1], "study": [2], "one": [3]},
            "open_access": {"oa_url": "https://oa.example.org/openalex-shared-1.pdf", "is_oa": True},
        },
        {
            "id": "https://openalex.org/W1002",
            "title": "OpenAlex overlap study 2",
            "authorships": [{"author": {"display_name": "Open Author Two"}}],
            "primary_location": {"source": {"display_name": "OpenAlex Journal"}},
            "publication_year": 2022,
            "doi": "https://doi.org/10.5555/shared-2",
            "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/900002"},
            "abstract_inverted_index": {"OpenAlex": [0], "overlap": [1], "study": [2], "two": [3]},
            "open_access": {"oa_url": "https://oa.example.org/openalex-shared-2.pdf", "is_oa": True},
        },
        {
            "id": "https://openalex.org/W1003",
            "title": "OpenAlex unique study 3",
            "authorships": [{"author": {"display_name": "Open Author Three"}}],
            "primary_location": {"source": {"display_name": "OpenAlex Journal"}},
            "publication_year": 2020,
            "doi": "https://doi.org/10.5555/openalex-3",
            "ids": {},
            "abstract_inverted_index": {"OpenAlex": [0], "unique": [1], "study": [2], "three": [3]},
            "open_access": {"oa_url": "https://oa.example.org/openalex-3.pdf", "is_oa": True},
        },
        {
            "id": "https://openalex.org/W1004",
            "title": "OpenAlex unique study 4",
            "authorships": [{"author": {"display_name": "Open Author Four"}}],
            "primary_location": {"source": {"display_name": "OpenAlex Journal"}},
            "publication_year": 2023,
            "doi": "https://doi.org/10.5555/openalex-4",
            "ids": {},
            "abstract_inverted_index": {"OpenAlex": [0], "unique": [1], "study": [2], "four": [3]},
            "open_access": {"oa_url": "https://oa.example.org/openalex-4.pdf", "is_oa": True},
        },
    ]

    europe_results = [
        {
            "id": "EPMC-1",
            "title": "Europe PMC overlap study",
            "authorString": "Miller J, Adams P",
            "journalTitle": "Europe Journal",
            "pubYear": "2022",
            "doi": "10.5555/shared-1",
            "pmid": "900001",
            "abstractText": "Europe overlap abstract.",
            "isOpenAccess": "Y",
        },
        {
            "id": "EPMC-2",
            "title": "Europe PMC unique study",
            "authorString": "Khan A",
            "journalTitle": "Europe Journal",
            "pubYear": "2021",
            "doi": "10.5555/europe-2",
            "pmid": "910002",
            "abstractText": "Europe unique abstract.",
            "isOpenAccess": "Y",
        },
    ]

    clinical_studies = [
        {
            "protocolSection": {
                "identificationModule": {"nctId": "NCT00000001", "briefTitle": "Metformin Trial A"},
                "contactsLocationsModule": {"overallOfficials": [{"name": "Dr Jane Doe"}]},
                "conditionsModule": {"conditions": ["Type 2 Diabetes"]},
                "armsInterventionsModule": {"interventions": [{"name": "Metformin"}]},
                "statusModule": {"startDateStruct": {"date": "2020-01-15"}},
                "descriptionModule": {"briefSummary": "Clinical trial summary A."},
            }
        },
        {
            "protocolSection": {
                "identificationModule": {"nctId": "NCT00000002", "briefTitle": "Metformin Trial B"},
                "contactsLocationsModule": {"overallOfficials": [{"name": "Dr Alex Smith"}]},
                "conditionsModule": {"conditions": ["Cardiovascular Disease"]},
                "armsInterventionsModule": {"interventions": [{"name": "Placebo"}]},
                "statusModule": {"startDateStruct": {"date": "2021-06-10"}},
                "descriptionModule": {"briefSummary": "Clinical trial summary B."},
            }
        },
    ]

    pmids = [record["pmid"] for record in pubmed_records]
    return {
        "pubmed_esearch_xml": _build_pubmed_esearch_xml(pmids),
        "pubmed_efetch_xml": _build_pubmed_efetch_xml(pubmed_records),
        "openalex_payload": {"results": openalex_results, "meta": {"next_cursor": None}},
        "europepmc_payload": {"resultList": {"result": europe_results}, "nextCursorMark": "*"},
        "clinicaltrials_payload": {"studies": clinical_studies},
    }


@pytest.fixture
def mock_external_apis(canned_responses: dict[str, Any]) -> dict[str, Any]:
    """Mock all external HTTP APIs used by the service graph."""
    with respx.mock(assert_all_called=False) as mock:
        routes: dict[str, Any] = {}
        routes["pubmed_esearch"] = mock.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        ).respond(status_code=200, text=canned_responses["pubmed_esearch_xml"])
        routes["pubmed_efetch"] = mock.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
        ).respond(status_code=200, text=canned_responses["pubmed_efetch_xml"])
        routes["openalex_works"] = mock.get("https://api.openalex.org/works").respond(
            status_code=200, json=canned_responses["openalex_payload"]
        )
        routes["europepmc_search"] = mock.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search").respond(
            status_code=200, json=canned_responses["europepmc_payload"]
        )
        routes["clinicaltrials_search"] = mock.get("https://clinicaltrials.gov/api/v2/studies").respond(
            status_code=200, json=canned_responses["clinicaltrials_payload"]
        )

        def _semantic_scholar_callback(request: httpx.Request) -> Response:
            identifier = request.url.path.rsplit("/", 1)[-1]
            payload = {
                "paperId": identifier,
                "title": f"Semantic Scholar paper {identifier}",
                "tldr": {"text": f"TLDR for {identifier}"},
                "citationCount": 17,
            }
            return Response(status_code=200, json=payload)

        routes["semantic_scholar"] = mock.get(
            re.compile(r"https://api\.semanticscholar\.org/graph/v1/paper/.*")
        ).mock(side_effect=_semantic_scholar_callback)

        def _unpaywall_callback(request: httpx.Request) -> Response:
            doi = request.url.path.rsplit("/v2/", 1)[-1]
            payload = {
                "doi": doi,
                "is_oa": True,
                "best_oa_location": {"url_for_pdf": f"https://oa.example.org/{doi}.pdf"},
            }
            return Response(status_code=200, json=payload)

        routes["unpaywall"] = mock.get(
            re.compile(r"https://api\.unpaywall\.org/v2/.*")
        ).mock(side_effect=_unpaywall_callback)

        routes["europepmc_fulltext_head"] = mock.head(
            re.compile(r"https://www\.ebi\.ac\.uk/europepmc/webservices/rest/.+/fullTextXML")
        ).respond(status_code=404)
        routes["europepmc_fulltext_get"] = mock.get(
            re.compile(r"https://www\.ebi\.ac\.uk/europepmc/webservices/rest/.+/fullTextXML")
        ).respond(status_code=404)

        yield routes


@pytest_asyncio.fixture
async def integration_context(mock_external_apis: dict[str, Any]) -> IntegrationContext:
    """Create a fully wired integration test context with mocked externals."""
    _ = mock_external_apis
    from src.main import app

    settings = get_settings()
    search_repo = InMemorySearchRepository()
    redis_client = InMemoryRedis()
    await redis_client.flushdb()

    http_client = httpx.AsyncClient()
    fetcher = FetcherService(client=http_client, redis_client=redis_client, settings=settings)
    s2_repo = SemanticScholarRepository(client=http_client, redis_client=redis_client, settings=settings)
    llm_client: LLMClient = FakeLLMClient()  # type: ignore[assignment]
    enrichment_service = EnrichmentService(
        s2_repo=s2_repo,
        llm_client=llm_client,
        redis_client=redis_client,
    )
    oa_service = OAService(
        openalex_repo=OpenAlexRepository(client=http_client, settings=settings),
        unpaywall_repo=UnpaywallRepository(client=http_client, redis_client=redis_client, settings=settings),
        europepmc_repo=EuropePMCRepository(client=http_client, settings=settings),
        redis_client=redis_client,
    )
    prisma_service = PrismaService()
    dedup_service = DedupService()
    search_service = SearchService(
        fetcher=fetcher,
        dedup=dedup_service,
        prisma=prisma_service,
        search_repo=search_repo,  # type: ignore[arg-type]
        redis_client=redis_client,  # type: ignore[arg-type]
        enrichment_service=enrichment_service,
        oa_service=oa_service,
    )
    streaming_search_service = StreamingSearchService(
        fetcher=fetcher,
        dedup=dedup_service,
        prisma=prisma_service,
        search_repo=search_repo,  # type: ignore[arg-type]
        redis_client=redis_client,  # type: ignore[arg-type]
        enrichment_service=enrichment_service,
        oa_service=oa_service,
    )

    app.dependency_overrides[deps.get_search_repo] = lambda: search_repo
    app.dependency_overrides[deps.get_redis] = lambda: redis_client
    app.dependency_overrides[deps.get_search_service] = lambda: search_service
    app.dependency_overrides[deps.get_streaming_search_service] = lambda: streaming_search_service
    app.dependency_overrides[deps.get_enrichment_service] = lambda: enrichment_service
    app.dependency_overrides[deps.get_oa_service] = lambda: oa_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield IntegrationContext(
            client=client,
            settings=settings,
            search_repo=search_repo,
            redis_client=redis_client,
            http_client=http_client,
            search_service=search_service,
            streaming_search_service=streaming_search_service,
        )

    app.dependency_overrides.clear()
    await redis_client.flushdb()
    await http_client.aclose()


@pytest_asyncio.fixture
async def integration_client(integration_context: IntegrationContext) -> AsyncClient:
    """Expose only the API client for tests that do not need internals."""
    return integration_context.client


def parse_sse_events(raw_body: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse SSE text/event-stream payload into event/data tuples."""
    events: list[tuple[str, dict[str, Any]]] = []
    current_event: str | None = None
    current_data_lines: list[str] = []

    for line in raw_body.splitlines():
        if line.startswith("event: "):
            current_event = line[7:].strip()
            continue
        if line.startswith("data: "):
            current_data_lines.append(line[6:].strip())
            continue
        if line.strip() == "" and current_event:
            payload = "\n".join(current_data_lines) if current_data_lines else "{}"
            events.append((current_event, json.loads(payload)))
            current_event = None
            current_data_lines = []

    if current_event:
        payload = "\n".join(current_data_lines) if current_data_lines else "{}"
        events.append((current_event, json.loads(payload)))

    return events


@pytest.fixture
def run_search(integration_client: AsyncClient):
    """Return helper to execute a search and capture search_id."""

    async def _run_search(**overrides: Any) -> tuple[str, httpx.Response]:
        payload = {
            "query": "metformin cardiovascular",
            "query_type": QueryType.FREE.value,
            "search_mode": SearchMode.QUICK.value,
        }
        payload.update(overrides)
        response = await integration_client.post("/api/v1/search", json=payload)
        body = response.json()
        search_id = body.get("search_id", "")
        return search_id, response

    return _run_search

