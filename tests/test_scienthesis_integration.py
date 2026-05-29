"""LitPortal ↔ Scienthesis integration tests.

Covers the three new units of integration:

1. **Identity JWKS validator** (``src.clients.identity_client.validate_identity_access_token``)
   — accepts Identity-issued RS256 access tokens; rejects garbage, wrong
   audience, expired tokens.
2. **UserRepository.upsert_identity_user** — lazy linking of an
   Identity-authenticated user to an existing LitPortal user row by email +
   ``identity_id`` stamping.
3. **LitHubSyncService** — best-effort save with outbox fallback when LitHub
   is unreachable; the outbox sweeper drains rows on retry.

These tests run as pure unit tests against in-memory SQLite + httpx mocks.
"""

from __future__ import annotations

import base64
import os
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import httpx
import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


# Ensure the litbridge-backend root is importable for `from src.* import …`.
# Conftest is in the same tests dir; pytest already injects the project root.


def _b64(value: int) -> str:
    n = (value.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(value.to_bytes(n, "big")).rstrip(b"=").decode()


@pytest.fixture(scope="module")
def rsa_keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    pub = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return priv, pub, key


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    """Required Settings env so `get_settings()` doesn't raise at import time."""
    monkeypatch.setenv("APP_NAME", "LitBridgeTest")
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("PORT", "8000")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/13")
    monkeypatch.setenv("NCBI_API_KEY", "test")
    monkeypatch.setenv("CONTACT_EMAIL", "test@example.com")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("OPENROUTER_MODEL", "")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-must-be-long-enough-32-chars")
    monkeypatch.setenv("IDENTITY_BASE_URL", "http://identity.test")
    monkeypatch.setenv("IDENTITY_JWKS_URL", "http://identity.test/.well-known/jwks.json")
    monkeypatch.setenv("IDENTITY_JWT_ISSUER", "scienthesis-identity")
    monkeypatch.setenv("IDENTITY_JWT_AUDIENCE", "litportal")
    monkeypatch.setenv("LITPORTAL_USE_IDENTITY", "true")
    monkeypatch.setenv("LITHUB_BASE_URL", "http://lithub.test")
    monkeypatch.setenv("LITPORTAL_DUAL_WRITE_LITHUB", "true")
    monkeypatch.setenv("SERVICE_TOKEN_SECRET", "shared-service-secret")
    # Reset cached settings.
    from src.core.config import get_settings as _gs

    _gs.cache_clear()


@pytest_asyncio.fixture
async def db_session():
    """Async SQLAlchemy session backed by aiosqlite.

    Only the tables our tests touch are created — the rest of the litbridge
    schema uses Postgres-native types (JSONB, ARRAY) that don't compile to
    SQLite.
    """
    from src.models import Base, LitHubSyncOutbox, RefreshToken, User

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    target_tables = [
        User.__table__,
        RefreshToken.__table__,
        LitHubSyncOutbox.__table__,
    ]
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(
                sync_conn, tables=target_tables,
            ),
        )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _mint(rsa_keypair, *, sub=None, email="alice@example.com", kid="test-key",
          aud="litportal", exp_delta=timedelta(hours=1)):
    priv, _, _ = rsa_keypair
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(sub or uuid4()),
        "email": email,
        "type": "access",
        "iss": "scienthesis-identity",
        "aud": aud,
        "iat": now,
        "exp": now + exp_delta,
    }
    return jwt.encode(payload, priv, algorithm="RS256", headers={"kid": kid})


@pytest_asyncio.fixture
async def mocked_http_client(rsa_keypair) -> AsyncIterator[httpx.AsyncClient]:
    """An httpx.AsyncClient with the JWKS endpoint stubbed via respx."""
    import respx

    _, pub_pem, _ = rsa_keypair
    pub = serialization.load_pem_public_key(pub_pem.encode())
    numbers = pub.public_numbers()  # type: ignore[attr-defined]
    body = {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": "test-key",
                "n": _b64(numbers.n),
                "e": _b64(numbers.e),
            }
        ]
    }
    router = respx.mock(assert_all_called=False, assert_all_mocked=False)
    router.get("http://identity.test/.well-known/jwks.json").mock(
        return_value=httpx.Response(200, json=body),
    )
    router.start()
    client = httpx.AsyncClient(timeout=httpx.Timeout(5.0))
    try:
        from src.clients.identity_client import reset_jwks_cache_for_tests
        reset_jwks_cache_for_tests()
        yield client
    finally:
        await client.aclose()
        router.stop()


# ── Identity validator ─────────────────────────────────────────────


async def test_identity_validator_accepts_valid_token(rsa_keypair, mocked_http_client):
    from src.clients.identity_client import validate_identity_access_token

    sub = uuid4()
    token = _mint(rsa_keypair, sub=sub, email="alice@example.com")
    payload = await validate_identity_access_token(token, http_client=mocked_http_client)
    assert payload is not None
    assert UUID(payload["sub"]) == sub
    assert payload["email"] == "alice@example.com"


async def test_identity_validator_accepts_real_multi_audience_token(rsa_keypair, mocked_http_client):
    """The real Identity token carries aud=[litpulse,litportal,lithub]; it must validate here."""
    from src.clients.identity_client import validate_identity_access_token

    sub = uuid4()
    token = _mint(rsa_keypair, sub=sub, aud=["litpulse", "litportal", "lithub"])
    payload = await validate_identity_access_token(token, http_client=mocked_http_client)
    assert payload is not None
    assert UUID(payload["sub"]) == sub


async def test_identity_validator_returns_none_for_hs256_token(rsa_keypair, mocked_http_client):
    from src.clients.identity_client import validate_identity_access_token

    legacy = jwt.encode(
        {"user_id": str(uuid4()), "type": "access", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        "test-secret-key-must-be-long-enough-32-chars",
        algorithm="HS256",
    )
    assert await validate_identity_access_token(legacy, http_client=mocked_http_client) is None


async def test_identity_validator_raises_on_expired_token(rsa_keypair, mocked_http_client):
    from src.clients.identity_client import validate_identity_access_token

    token = _mint(rsa_keypair, exp_delta=timedelta(seconds=-1))
    with pytest.raises(jwt.InvalidTokenError):
        await validate_identity_access_token(token, http_client=mocked_http_client)


async def test_identity_validator_returns_none_for_unknown_kid(rsa_keypair, mocked_http_client):
    from src.clients.identity_client import validate_identity_access_token

    token = _mint(rsa_keypair, kid="unknown-key")
    assert await validate_identity_access_token(token, http_client=mocked_http_client) is None


# ── User upsert ────────────────────────────────────────────────────


async def test_upsert_identity_user_creates_new(db_session):
    from src.repositories.user_repo import UserRepository

    repo = UserRepository(db=db_session)
    identity_id = uuid4()
    user = await repo.upsert_identity_user(identity_id=identity_id, email="alice@example.com")
    assert user.identity_id == identity_id
    assert user.email == "alice@example.com"
    assert user.is_verified is True
    assert user.auth_provider == "identity"


async def test_upsert_identity_user_idempotent_on_identity_id(db_session):
    from src.repositories.user_repo import UserRepository

    repo = UserRepository(db=db_session)
    identity_id = uuid4()
    first = await repo.upsert_identity_user(identity_id=identity_id, email="alice@example.com")
    second = await repo.upsert_identity_user(identity_id=identity_id, email="alice@example.com")
    assert first.id == second.id


async def test_upsert_identity_user_links_existing_by_email(db_session):
    from src.models.user import User
    from src.repositories.user_repo import UserRepository

    repo = UserRepository(db=db_session)
    existing = User(email="alice@example.com", auth_provider="email")
    db_session.add(existing)
    await db_session.commit()
    await db_session.refresh(existing)

    identity_id = uuid4()
    linked = await repo.upsert_identity_user(identity_id=identity_id, email="ALICE@example.com")
    assert linked.id == existing.id
    assert linked.identity_id == identity_id


async def test_upsert_identity_user_with_empty_email_creates_synthetic(db_session):
    from src.repositories.user_repo import UserRepository

    repo = UserRepository(db=db_session)
    identity_id = uuid4()
    user = await repo.upsert_identity_user(identity_id=identity_id, email="")
    assert user.identity_id == identity_id
    assert "scienthesis.local" in user.email


# ── LitHub sync (best-effort + outbox) ──────────────────────────────


@pytest_asyncio.fixture
async def lithub_router():
    """Respx router that defaults to a 500 response from LitHub (simulates outage)."""
    import respx

    router = respx.mock(assert_all_called=False, assert_all_mocked=False)
    router.start()
    yield router
    router.stop()


async def test_save_paper_succeeds_when_lithub_returns_200(
    db_session, lithub_router,
):
    from src.clients.lithub_client import LitHubClient
    from src.repositories.lithub_sync_repo import LitHubSyncRepository
    from src.services.lithub_sync_service import LitHubSyncService

    lithub_router.post("http://lithub.test/api/v1/library/save").mock(
        return_value=httpx.Response(
            200,
            json={
                "message": "ok",
                "article_id": str(uuid4()),
                "paper_id": str(uuid4()),
                "library_entry_id": str(uuid4()),
                "dedup_key": "pmid:123",
                "saved_at": datetime.now(timezone.utc).isoformat(),
            },
        ),
    )
    async with httpx.AsyncClient() as client:
        lithub = LitHubClient(http_client=client)
        outbox = LitHubSyncRepository(db=db_session)
        sync = LitHubSyncService(lithub=lithub, outbox=outbox)
        ok, _ = await sync.save_paper(
            uuid4(), {"pmid": "123", "title": "T"}, access_token="user-token",
        )
        assert ok is True
        # Nothing in the outbox.
        due = await outbox.fetch_due()
        assert due == []


async def test_save_paper_falls_back_to_outbox_on_lithub_5xx(
    db_session, lithub_router,
):
    from src.clients.lithub_client import LitHubClient
    from src.repositories.lithub_sync_repo import LitHubSyncRepository
    from src.services.lithub_sync_service import LitHubSyncService

    lithub_router.post("http://lithub.test/api/v1/library/save").mock(
        return_value=httpx.Response(503, text="LitHub is down"),
    )
    async with httpx.AsyncClient() as client:
        lithub = LitHubClient(http_client=client)
        outbox = LitHubSyncRepository(db=db_session)
        sync = LitHubSyncService(lithub=lithub, outbox=outbox)
        user_id = uuid4()
        ok, _ = await sync.save_paper(
            user_id, {"pmid": "999", "title": "T"}, access_token="user-token",
        )
        assert ok is False
        due = await outbox.fetch_due()
        assert len(due) == 1
        assert due[0].user_id == user_id
        assert "down" in (due[0].last_error or "").lower() or due[0].last_error


async def test_drain_outbox_clears_pending_on_eventual_success(
    db_session, lithub_router,
):
    from src.clients.lithub_client import LitHubClient
    from src.repositories.lithub_sync_repo import LitHubSyncRepository
    from src.services.lithub_sync_service import LitHubSyncService

    user_id = uuid4()
    # Pre-seed the outbox as if a prior request had failed.
    outbox = LitHubSyncRepository(db=db_session)
    await outbox.enqueue(user_id, {"pmid": "42", "title": "Towards Answers"})

    lithub_router.post("http://lithub.test/api/v1/internal/library/bulk-import").mock(
        return_value=httpx.Response(
            200,
            json={
                "user_id": str(user_id),
                "imported": 1,
                "skipped_duplicate": 0,
                "skipped_invalid": 0,
                "articles": [
                    {
                        "pmid": "42",
                        "doi": None,
                        "title": "Towards Answers",
                        "journal": None,
                        "pub_date": None,
                        "authors": None,
                        "abstract": None,
                        "ai_summary": None,
                        "design_tags": None,
                        "url": None,
                        "saved_at": datetime.now(timezone.utc).isoformat(),
                        "folder": "Inbox",
                        "paper_id": str(uuid4()),
                        "library_entry_id": str(uuid4()),
                        "source": "search",
                        "full_text_status": None,
                        "best_full_text_url": None,
                        "recommended": False,
                        "selected": False,
                        "answer_context_id": None,
                        "portal_engine_record_id": None,
                        "notes": None,
                    }
                ],
            },
        ),
    )
    async with httpx.AsyncClient() as client:
        lithub = LitHubClient(http_client=client)
        sync = LitHubSyncService(lithub=lithub, outbox=outbox)
        sent = await sync.drain_outbox()
        assert sent == 1


async def test_outbox_marks_dead_after_max_attempts(db_session):
    from src.repositories.lithub_sync_repo import LitHubSyncRepository

    outbox = LitHubSyncRepository(db=db_session)
    row = await outbox.enqueue(uuid4(), {"pmid": "boom", "title": "x"})
    for _ in range(5):
        await outbox.mark_failed(row, "boom")
    await db_session.refresh(row)
    assert row.status == "dead"
    assert row.attempts == 5
