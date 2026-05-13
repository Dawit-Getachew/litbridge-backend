"""Tests for the LitPulse cross-service auth bridge.

Verifies that ``get_current_user`` (in ``src.core.deps``) accepts:
  1. Native Portal Engine OTP-issued JWTs (existing behavior, no regression).
  2. LitPulse-issued JWTs when ``LITPULSE_JWT_ENABLED`` is true and signed
     with ``LITPULSE_JWT_SECRET_KEY``.
And rejects:
  3. LitPulse-shaped tokens when the bridge is disabled.
  4. Tokens signed with the wrong secret.
  5. LitPulse tokens missing required claims (`user_id`, `email`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import jwt
import pytest

from src.core import deps
from src.core.config import get_settings
from src.core.exceptions import AuthenticationError
from src.models.user import User


def _make_litpulse_token(
    secret: str,
    *,
    user_id: str = "litpulse-user-abc",
    email: str = "user@example.com",
    token_type: str = "access",
    exp_minutes: int = 60,
) -> str:
    payload = {
        "user_id": user_id,
        "email": email,
        "type": token_type,
        "exp": datetime.now(UTC) + timedelta(minutes=exp_minutes),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def _make_native_token(secret: str, *, sub: str | None = None) -> str:
    payload = {
        "sub": sub or str(uuid4()),
        "email": "native@example.com",
        "provider": "email",
        "type": "access",
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(minutes=30),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def _bearer(token: str):
    creds = MagicMock()
    creds.credentials = token
    return creds


def _make_user(*, user_id=None, litpulse_user_id: str | None = None) -> User:
    user = User(
        email="user@example.com",
        is_active=True,
        is_verified=True,
        auth_provider="litpulse" if litpulse_user_id else "email",
        litpulse_user_id=litpulse_user_id,
    )
    user.id = user_id or uuid4()
    return user


@pytest.mark.asyncio
async def test_native_token_path_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """A valid native OTP token still resolves the Portal Engine user."""
    monkeypatch.setenv("LITPULSE_JWT_ENABLED", "false")
    get_settings.cache_clear()

    user = _make_user()
    token = _make_native_token(get_settings().SECRET_KEY, sub=str(user.id))

    repo = MagicMock()
    repo.get_by_id = AsyncMock(return_value=user)
    repo.upsert_litpulse_user = AsyncMock()

    result = await deps.get_current_user(token=_bearer(token), user_repo=repo)
    assert result is user
    repo.get_by_id.assert_awaited_once()
    repo.upsert_litpulse_user.assert_not_awaited()


@pytest.mark.asyncio
async def test_litpulse_token_accepted_when_bridge_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the bridge is on, a LitPulse-signed JWT upserts and returns a user."""
    monkeypatch.setenv("LITPULSE_JWT_ENABLED", "true")
    monkeypatch.setenv("LITPULSE_JWT_SECRET_KEY", "litpulse-test-secret-key")
    get_settings.cache_clear()

    expected_user = _make_user(litpulse_user_id="litpulse-user-abc")
    token = _make_litpulse_token("litpulse-test-secret-key")

    repo = MagicMock()
    repo.get_by_id = AsyncMock()  # Should NOT be hit — native decode fails first.
    repo.upsert_litpulse_user = AsyncMock(return_value=expected_user)

    result = await deps.get_current_user(token=_bearer(token), user_repo=repo)
    assert result is expected_user
    repo.upsert_litpulse_user.assert_awaited_once_with(
        litpulse_user_id="litpulse-user-abc",
        email="user@example.com",
    )


@pytest.mark.asyncio
async def test_litpulse_token_rejected_when_bridge_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LitPulse-shaped token is rejected with 401 when the bridge is off."""
    monkeypatch.setenv("LITPULSE_JWT_ENABLED", "false")
    monkeypatch.setenv("LITPULSE_JWT_SECRET_KEY", "litpulse-test-secret-key")
    get_settings.cache_clear()

    token = _make_litpulse_token("litpulse-test-secret-key")
    repo = MagicMock()
    repo.get_by_id = AsyncMock(return_value=None)
    repo.upsert_litpulse_user = AsyncMock()

    with pytest.raises(AuthenticationError):
        await deps.get_current_user(token=_bearer(token), user_repo=repo)
    repo.upsert_litpulse_user.assert_not_awaited()


@pytest.mark.asyncio
async def test_token_with_wrong_secret_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A token signed with neither secret returns 401."""
    monkeypatch.setenv("LITPULSE_JWT_ENABLED", "true")
    monkeypatch.setenv("LITPULSE_JWT_SECRET_KEY", "litpulse-test-secret-key")
    get_settings.cache_clear()

    token = _make_litpulse_token("attacker-fabricated-secret")
    repo = MagicMock()
    repo.get_by_id = AsyncMock()
    repo.upsert_litpulse_user = AsyncMock()

    with pytest.raises(AuthenticationError):
        await deps.get_current_user(token=_bearer(token), user_repo=repo)


@pytest.mark.asyncio
async def test_litpulse_token_missing_email_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A LitPulse-signed token without `email` claim raises 401, not silent skip."""
    monkeypatch.setenv("LITPULSE_JWT_ENABLED", "true")
    monkeypatch.setenv("LITPULSE_JWT_SECRET_KEY", "litpulse-test-secret-key")
    get_settings.cache_clear()

    payload = {
        "user_id": "litpulse-user-abc",
        # No email
        "type": "access",
        "exp": datetime.now(UTC) + timedelta(minutes=10),
    }
    token = jwt.encode(payload, "litpulse-test-secret-key", algorithm="HS256")

    repo = MagicMock()
    repo.get_by_id = AsyncMock()
    repo.upsert_litpulse_user = AsyncMock()

    with pytest.raises(AuthenticationError):
        await deps.get_current_user(token=_bearer(token), user_repo=repo)
    repo.upsert_litpulse_user.assert_not_awaited()
