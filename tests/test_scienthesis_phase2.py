"""LitPortal Phase-2 completion tests.

Covers:
  * AuthService delegation to Identity (request-otp / verify-otp / refresh /
    logout) — preserving the TokenResponse shape.
  * ResearchCollectionService enrichment from LitHub (study_design / key_findings
    backfill) — preserving the PaperMetadata shape.
  * _merge_lithub_metadata semantics (local LLM metadata always wins).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest
import respx

from src.core.config import get_settings


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setenv("APP_NAME", "LitBridgeTest")
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("PORT", "8000")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/13")
    monkeypatch.setenv("NCBI_API_KEY", "x")
    monkeypatch.setenv("CONTACT_EMAIL", "a@b.com")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("OPENROUTER_MODEL", "")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-32-chars-minimum-aaaaaaaa")
    monkeypatch.setenv("IDENTITY_BASE_URL", "http://identity.test")
    monkeypatch.setenv("IDENTITY_JWT_ISSUER", "scienthesis-identity")
    monkeypatch.setenv("IDENTITY_JWT_AUDIENCE", "litportal")
    monkeypatch.setenv("LITPORTAL_USE_IDENTITY", "true")
    monkeypatch.setenv("LITHUB_BASE_URL", "http://lithub.test")
    monkeypatch.setenv("LITPORTAL_DUAL_WRITE_LITHUB", "true")
    monkeypatch.setenv("SERVICE_TOKEN_SECRET", "shared-secret")
    get_settings.cache_clear()


# ── AuthService delegation ──────────────────────────────────────────


class _FakeRedis:
    async def get(self, k):
        return None

    async def setex(self, *a):
        return True

    async def delete(self, *a):
        return 1

    async def ttl(self, k):
        return 0

    def pipeline(self):
        return self

    def incr(self, k):
        return self

    def expire(self, k, s):
        return self

    async def execute(self):
        return []


class _FakeIdentityClient:
    def __init__(self):
        self.calls = []

    async def request_otp(self, email):
        self.calls.append(("request_otp", email))
        return {"message": "sent"}

    async def verify_otp(self, email, code):
        self.calls.append(("verify_otp", email, code))
        return {
            "access_token": "identity-access", "refresh_token": "identity-refresh",
            "token_type": "bearer", "expires_in": 1800,
        }

    async def refresh(self, refresh_token):
        self.calls.append(("refresh", refresh_token))
        return {
            "access_token": "new-access", "refresh_token": "new-refresh",
            "token_type": "bearer", "expires_in": 1800,
        }

    async def logout(self, refresh_token, access_token):
        self.calls.append(("logout", refresh_token, access_token))
        return {}


def _auth_service(identity_client):
    from src.services.auth_service import AuthService

    return AuthService(
        user_repo=None,  # not used on the delegated paths
        email_service=None,
        redis_client=_FakeRedis(),
        settings=get_settings(),
        identity_client=identity_client,
    )


async def test_request_otp_delegates_to_identity():
    fake = _FakeIdentityClient()
    svc = _auth_service(fake)
    await svc.request_otp("alice@example.com")
    assert ("request_otp", "alice@example.com") in fake.calls


async def test_verify_otp_delegates_and_preserves_token_shape():
    fake = _FakeIdentityClient()
    svc = _auth_service(fake)
    token = await svc.verify_otp("alice@example.com", "123456")
    assert token.access_token == "identity-access"
    assert token.refresh_token == "identity-refresh"
    assert token.token_type == "bearer"
    assert token.expires_in == 1800
    assert ("verify_otp", "alice@example.com", "123456") in fake.calls


async def test_refresh_delegates_to_identity():
    fake = _FakeIdentityClient()
    svc = _auth_service(fake)
    token = await svc.refresh_tokens("old-refresh")
    assert token.access_token == "new-access"
    assert ("refresh", "old-refresh") in fake.calls


async def test_logout_delegates_to_identity():
    fake = _FakeIdentityClient()
    svc = _auth_service(fake)
    await svc.logout("refresh-tok", access_token="access-tok")
    assert ("logout", "refresh-tok", "access-tok") in fake.calls


async def test_auth_service_not_delegated_when_identity_disabled(monkeypatch):
    monkeypatch.setenv("LITPORTAL_USE_IDENTITY", "false")
    get_settings.cache_clear()
    fake = _FakeIdentityClient()
    svc = _auth_service(fake)
    assert svc._identity_enabled is False


# ── _merge_lithub_metadata semantics ────────────────────────────────


def test_merge_backfills_empty_fields():
    from src.schemas.research_collection import PaperMetadata
    from src.services.research_collection_service import _merge_lithub_metadata

    base = PaperMetadata()  # all "Not reported" / None
    paper = {
        "study_design": "rct",
        "ai_summary": "The trial showed a 20% reduction in mortality.",
        "journal": "NEJM",
        "pub_date": "2024",
    }
    merged = _merge_lithub_metadata(base, paper)
    assert merged.key_findings.startswith("The trial showed")
    assert "NEJM" in merged.study_details


def test_merge_does_not_override_local_llm_metadata():
    from src.schemas.research_collection import PaperMetadata
    from src.services.research_collection_service import _merge_lithub_metadata

    local = PaperMetadata(key_findings="LLM-extracted finding", study_details="LLM details")
    paper = {"ai_summary": "LitHub summary", "journal": "NEJM"}
    merged = _merge_lithub_metadata(local, paper)
    assert merged.key_findings == "LLM-extracted finding"   # local wins
    assert merged.study_details == "LLM details"


def test_merge_invalid_study_design_keeps_metadata():
    from src.schemas.research_collection import PaperMetadata
    from src.services.research_collection_service import _merge_lithub_metadata

    base = PaperMetadata()
    paper = {"study_design": "not-a-valid-enum-value-xyz"}
    merged = _merge_lithub_metadata(base, paper)
    # Invalid enum → merge falls back to the original metadata (no crash).
    assert merged.study_design is None


# ── ResearchCollectionService enrichment (fetch path) ───────────────


class _FakeLitHubClient:
    def __init__(self, papers):
        self._papers = papers
        self.bulk_calls = 0

    async def internal_papers_bulk(self, paper_ids):
        self.bulk_calls += 1
        wanted = {str(p) for p in paper_ids}
        return [p for p in self._papers if str(p["paper_id"]) in wanted]


class _FakeItem:
    def __init__(self, paper_id=None, metadata_extracted=None):
        self.id = uuid4()
        self.collection_id = uuid4()
        self.record_id = "rec-1"
        self.search_session_id = uuid4()
        self.title = "T"
        self.notes = None
        self.metadata_extracted = metadata_extracted
        self.paper_id = paper_id
        self.created_at = datetime.now(timezone.utc)


async def test_service_enriches_item_from_lithub():
    from src.services.research_collection_service import ResearchCollectionService

    paper_id = uuid4()
    lithub = _FakeLitHubClient([
        {"paper_id": str(paper_id), "study_design": "rct",
         "ai_summary": "Key result here.", "journal": "Lancet", "pub_date": "2023"},
    ])
    svc = ResearchCollectionService(repo=None, lithub_client=lithub, lithub_enabled=True)
    item = _FakeItem(paper_id=paper_id, metadata_extracted=None)

    papers = await svc._fetch_lithub_papers([paper_id])
    resp = svc._item_to_response(item, papers)
    assert resp.metadata.key_findings == "Key result here."
    assert lithub.bulk_calls == 1


async def test_service_enrichment_disabled_returns_empty_map():
    from src.services.research_collection_service import ResearchCollectionService

    svc = ResearchCollectionService(repo=None, lithub_client=None, lithub_enabled=False)
    papers = await svc._fetch_lithub_papers([uuid4()])
    assert papers == {}


async def test_service_enrichment_best_effort_on_lithub_error():
    from src.services.research_collection_service import ResearchCollectionService

    class _Boom:
        async def internal_papers_bulk(self, ids):
            raise RuntimeError("lithub down")

    svc = ResearchCollectionService(repo=None, lithub_client=_Boom(), lithub_enabled=True)
    papers = await svc._fetch_lithub_papers([uuid4()])
    assert papers == {}  # swallowed; caller falls back to local metadata


# ── lithub_client internal_save_paper uses the dedicated endpoint ───


async def test_lithub_client_construction_tolerant_of_unset_base(monkeypatch):
    """Regression: constructing a LitHubClient with no base must NOT raise, so
    dependency wiring never crashes an un-configured deploy; the error surfaces
    only when an actual request is attempted."""
    monkeypatch.setenv("LITHUB_BASE_URL", "")
    get_settings.cache_clear()
    from src.clients.lithub_client import LitHubClient, LitHubUpstreamError

    async with httpx.AsyncClient() as client:
        lithub = LitHubClient(http_client=client)  # must not raise
        with pytest.raises(LitHubUpstreamError):
            await lithub.internal_membership(uuid4(), pmid="1")


async def test_identity_client_construction_tolerant_of_unset_base(monkeypatch):
    monkeypatch.setenv("IDENTITY_BASE_URL", "")
    get_settings.cache_clear()
    from src.clients.identity_client import IdentityClient, IdentityUpstreamError

    async with httpx.AsyncClient() as client:
        ident = IdentityClient(http_client=client)  # must not raise
        with pytest.raises(IdentityUpstreamError):
            await ident.request_otp("a@b.com")


@respx.mock
async def test_internal_save_paper_hits_save_endpoint_and_returns_paper_id():
    from src.clients.lithub_client import LitHubClient

    paper_id = str(uuid4())
    route = respx.post("http://lithub.test/api/v1/internal/library/save").mock(
        return_value=httpx.Response(200, json={
            "message": "ok", "article_id": str(uuid4()), "paper_id": paper_id,
            "library_entry_id": str(uuid4()), "dedup_key": "pmid:1",
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }),
    )
    user_id = uuid4()
    async with httpx.AsyncClient() as client:
        lithub = LitHubClient(http_client=client)
        result = await lithub.internal_save_paper(user_id, {"pmid": "1", "title": "T"})
    assert route.called
    body = json.loads(route.calls.last.request.content)
    assert body["user_id"] == str(user_id)
    assert body["item"]["pmid"] == "1"
    assert result["paper_id"] == paper_id
