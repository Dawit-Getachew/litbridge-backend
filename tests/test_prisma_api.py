"""API tests for PRISMA counts endpoint."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

from src.core import deps
from src.schemas.enums import AgeGroup, OAStatus, SourceType, StudyDesign
from src.schemas.records import UnifiedRecord


@dataclass
class FakeSearchSession:
    """In-memory session model used by PRISMA endpoint tests."""

    id: UUID
    total_identified: int
    results: list[dict]


class InMemorySearchRepository:
    """Minimal repository supporting session lookup by search id."""

    def __init__(self, session: FakeSearchSession) -> None:
        self.session = session

    async def get_session(self, search_id: str) -> FakeSearchSession | None:
        if search_id == str(self.session.id):
            return self.session
        return None


@pytest.fixture
def prisma_api_context(async_client: AsyncClient):
    """Override search repository dependency for PRISMA endpoint tests."""
    from src.main import app

    records = [
        UnifiedRecord(
            id="rec-1",
            title="Study 1",
            authors=["Author One"],
            year=2019,
            source=SourceType.PUBMED,
            sources_found_in=[SourceType.PUBMED],
            oa_status=OAStatus.OPEN,
            age_groups=[AgeGroup.ADULT],
            age_min=18,
            age_max=65,
            study_design=StudyDesign.RCT,
        ),
        UnifiedRecord(
            id="rec-2",
            title="Study 2",
            authors=["Author Two"],
            year=2020,
            source=SourceType.OPENALEX,
            sources_found_in=[SourceType.OPENALEX],
            oa_status=OAStatus.CLOSED,
            age_groups=[AgeGroup.CHILD],
            age_min=0,
            age_max=17,
            study_design=StudyDesign.OBSERVATIONAL,
        ),
        UnifiedRecord(
            id="rec-3",
            title="Study 3",
            authors=["Author Three"],
            year=2021,
            source=SourceType.EUROPEPMC,
            sources_found_in=[SourceType.EUROPEPMC],
            oa_status=OAStatus.OPEN,
            age_groups=[AgeGroup.ADULT, AgeGroup.OLDER_ADULT],
            age_min=18,
            age_max=90,
            study_design=StudyDesign.RCT,
        ),
        UnifiedRecord(
            id="rec-4",
            title="Study 4",
            authors=["Author Four"],
            year=2022,
            source=SourceType.PUBMED,
            sources_found_in=[SourceType.PUBMED],
            oa_status=OAStatus.UNKNOWN,
            age_groups=[AgeGroup.OLDER_ADULT],
            age_min=65,
            age_max=100,
            study_design=StudyDesign.SYSTEMATIC_REVIEW,
        ),
        UnifiedRecord(
            id="rec-5",
            title="Study 5",
            authors=["Author Five"],
            year=2023,
            source=SourceType.CLINICALTRIALS,
            sources_found_in=[SourceType.CLINICALTRIALS],
            oa_status=OAStatus.OPEN,
        ),
    ]

    session = FakeSearchSession(
        id=uuid4(),
        total_identified=9,
        results=[record.model_dump(mode="json") for record in records],
    )
    search_repo = InMemorySearchRepository(session)

    app.dependency_overrides[deps.get_search_repo] = lambda: search_repo
    yield async_client, str(session.id)
    app.dependency_overrides.pop(deps.get_search_repo, None)


@pytest.mark.asyncio
async def test_get_prisma_returns_correct_counts(prisma_api_context) -> None:
    client, search_id = prisma_api_context
    response = await client.get(f"/api/v1/prisma/{search_id}")
    body = response.json()

    assert response.status_code == 200
    assert body["identified"] == 9
    assert body["after_deduplication"] == 5
    assert body["screened"] == 5
    assert body["excluded"] == 0
    assert body["oa_retrieved"] == 3


@pytest.mark.asyncio
async def test_get_prisma_with_year_range_filters(prisma_api_context) -> None:
    client, search_id = prisma_api_context
    response = await client.get(
        f"/api/v1/prisma/{search_id}",
        params={"year_from": 2020, "year_to": 2022},
    )
    body = response.json()

    assert response.status_code == 200
    assert body["after_deduplication"] == 5
    assert body["screened"] == 5
    assert body["excluded"] == 2
    assert body["oa_retrieved"] == 1


@pytest.mark.asyncio
async def test_get_prisma_with_sources_filter(prisma_api_context) -> None:
    client, search_id = prisma_api_context
    response = await client.get(
        f"/api/v1/prisma/{search_id}",
        params={"sources": "pubmed,openalex"},
    )
    body = response.json()

    assert response.status_code == 200
    assert body["excluded"] == 2
    assert body["oa_retrieved"] == 1


@pytest.mark.asyncio
async def test_get_prisma_with_open_access_only_filter(prisma_api_context) -> None:
    client, search_id = prisma_api_context
    response = await client.get(
        f"/api/v1/prisma/{search_id}",
        params={"open_access_only": "true"},
    )
    body = response.json()

    assert response.status_code == 200
    assert body["excluded"] == 2
    assert body["oa_retrieved"] == 3


@pytest.mark.asyncio
async def test_get_prisma_with_all_filters_combined(prisma_api_context) -> None:
    client, search_id = prisma_api_context
    response = await client.get(
        f"/api/v1/prisma/{search_id}",
        params={
            "year_from": 2020,
            "year_to": 2023,
            "sources": "pubmed,europepmc",
            "open_access_only": "true",
        },
    )
    body = response.json()

    assert response.status_code == 200
    assert body["excluded"] == 4
    assert body["oa_retrieved"] == 1


@pytest.mark.asyncio
async def test_get_prisma_with_age_group_filter(prisma_api_context) -> None:
    client, search_id = prisma_api_context
    response = await client.get(
        f"/api/v1/prisma/{search_id}",
        params={"age_group": "adult"},
    )
    body = response.json()

    assert response.status_code == 200
    assert body["excluded"] == 3
    assert body["oa_retrieved"] == 2


@pytest.mark.asyncio
async def test_get_prisma_with_age_range_filter(prisma_api_context) -> None:
    client, search_id = prisma_api_context
    response = await client.get(
        f"/api/v1/prisma/{search_id}",
        params={"age_min": 18, "age_max": 65},
    )
    body = response.json()

    assert response.status_code == 200
    assert body["excluded"] == 1
    assert body["oa_retrieved"] == 3


@pytest.mark.asyncio
async def test_get_prisma_with_study_design_filter(prisma_api_context) -> None:
    client, search_id = prisma_api_context
    response = await client.get(
        f"/api/v1/prisma/{search_id}",
        params={"study_design": "rct"},
    )
    body = response.json()

    assert response.status_code == 200
    # 2 records (rec-1, rec-3) match rct; 3 are excluded
    assert body["excluded"] == 3
    assert body["oa_retrieved"] == 2


@pytest.mark.asyncio
async def test_get_prisma_with_multiple_study_designs(prisma_api_context) -> None:
    client, search_id = prisma_api_context
    response = await client.get(
        f"/api/v1/prisma/{search_id}",
        params={"study_design": "rct,observational"},
    )
    body = response.json()

    assert response.status_code == 200
    # 3 records (rec-1, rec-2, rec-3) match; 2 excluded (rec-4 systematic_review, rec-5 None)
    assert body["excluded"] == 2
    assert body["oa_retrieved"] == 2


@pytest.mark.asyncio
async def test_get_prisma_old_study_type_param_rejected(prisma_api_context) -> None:
    """Hard cutover: old query param name is no longer recognized as a filter.

    FastAPI silently ignores unknown query params, so the result is the same
    as no filter at all (not a 422). We assert that the response matches
    the no-filter behavior to confirm the param really isn't being read.
    """
    client, search_id = prisma_api_context
    response = await client.get(
        f"/api/v1/prisma/{search_id}",
        params={"study_type": "interventional"},
    )
    body = response.json()

    assert response.status_code == 200
    # No-filter excluded == 0 (matches test_get_prisma_returns_correct_counts above)
    assert body["excluded"] == 0


@pytest.mark.asyncio
async def test_get_prisma_with_invalid_age_group_returns_422(prisma_api_context) -> None:
    client, search_id = prisma_api_context
    response = await client.get(
        f"/api/v1/prisma/{search_id}",
        params={"age_group": "teenager"},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_prisma_with_invalid_study_design_returns_422(prisma_api_context) -> None:
    client, search_id = prisma_api_context
    response = await client.get(
        f"/api/v1/prisma/{search_id}",
        params={"study_design": "invalid_design"},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_prisma_with_unknown_search_id_returns_404(prisma_api_context) -> None:
    client, _search_id = prisma_api_context
    response = await client.get(f"/api/v1/prisma/{uuid4()}")

    assert response.status_code == 404
