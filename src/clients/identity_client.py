"""HTTP client + JWKS validator for the Scienthesis Identity Service."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import UUID

import httpx
import jwt
import structlog
from jwt import algorithms

from src.core.config import Settings, get_settings
from src.clients.service_token import mint_service_token

logger = structlog.get_logger(__name__)


class _JWKSCache:
    """Async-safe in-process JWKS cache."""

    def __init__(self) -> None:
        self._keys_by_kid: dict[str, Any] = {}
        self._fetched_at: float = 0.0
        self._lock = asyncio.Lock()

    def _fresh(self, ttl: int) -> bool:
        return bool(self._keys_by_kid) and (time.time() - self._fetched_at) < ttl

    async def fetch(self, http_client: httpx.AsyncClient, settings: Settings) -> dict[str, Any]:
        if self._fresh(settings.JWKS_CACHE_TTL_SECONDS):
            return self._keys_by_kid
        async with self._lock:
            if self._fresh(settings.JWKS_CACHE_TTL_SECONDS):
                return self._keys_by_kid
            url = settings.IDENTITY_JWKS_URL or (
                f"{settings.IDENTITY_BASE_URL.rstrip('/')}/.well-known/jwks.json"
            )
            try:
                resp = await http_client.get(url, timeout=8.0)
                resp.raise_for_status()
                body = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                logger.error("identity_jwks_fetch_failed", url=url, error=str(exc))
                return self._keys_by_kid
            self._keys_by_kid = {
                k["kid"]: k for k in body.get("keys", []) if "kid" in k
            }
            self._fetched_at = time.time()
            return self._keys_by_kid

    def clear(self) -> None:
        self._keys_by_kid = {}
        self._fetched_at = 0.0


_jwks_cache = _JWKSCache()


def reset_jwks_cache_for_tests() -> None:
    _jwks_cache.clear()


async def validate_identity_access_token(
    token: str,
    *,
    http_client: httpx.AsyncClient,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    """Verify an Identity-issued RS256 token. Returns ``None`` when the token
    is not Identity-shaped (caller can fall back). Raises ``jwt.InvalidTokenError``
    for tokens that ARE Identity-shaped but invalid (expired, bad signature)."""
    settings = settings or get_settings()
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError:
        return None
    if header.get("alg") != "RS256":
        return None
    kid = header.get("kid")
    if not kid:
        return None

    keys = await _jwks_cache.fetch(http_client, settings)
    key_dict = keys.get(kid)
    if key_dict is None:
        _jwks_cache.clear()
        keys = await _jwks_cache.fetch(http_client, settings)
        key_dict = keys.get(kid)
        if key_dict is None:
            return None

    public_key = algorithms.RSAAlgorithm.from_jwk(key_dict)
    payload = jwt.decode(
        token,
        public_key,
        algorithms=["RS256"],
        audience=settings.IDENTITY_JWT_AUDIENCE,
        issuer=settings.IDENTITY_JWT_ISSUER,
    )
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("Identity token type is not 'access'.")
    return payload


# ── HTTP client ────────────────────────────────────────────────────


class IdentityClientError(Exception):
    def __init__(self, status_code: int, detail: Any) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Identity {status_code}: {detail}")


class IdentityUpstreamError(Exception):
    """Identity unreachable or 5xx."""


class IdentityClient:
    """Async client for Identity auth + internal calls."""

    def __init__(self, http_client: httpx.AsyncClient, settings: Settings | None = None) -> None:
        self._client = http_client
        self._settings = settings or get_settings()
        # Tolerate an unset base at construction; raise at call time instead so
        # dependency wiring never crashes an un-configured deploy.
        self._base = (self._settings.IDENTITY_BASE_URL or "").rstrip("/")

    def _require_base(self) -> str:
        if not self._base:
            raise IdentityUpstreamError("IDENTITY_BASE_URL is not configured.")
        return self._base

    async def signup(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/api/v1/auth/signup", json=body)

    async def login(self, email: str, password: str) -> dict[str, Any]:
        return await self._request(
            "POST", "/api/v1/auth/login", json={"email": email, "password": password},
        )

    async def request_otp(self, email: str) -> dict[str, Any]:
        return await self._request("POST", "/api/v1/auth/request-otp", json={"email": email})

    async def verify_otp(self, email: str, code: str) -> dict[str, Any]:
        return await self._request(
            "POST", "/api/v1/auth/verify-otp", json={"email": email, "code": code},
        )

    async def verify_email_code(self, email: str, code: str) -> dict[str, Any]:
        return await self._request(
            "POST", "/api/v1/auth/verify-code", json={"email": email, "code": code},
        )

    async def resend_verification(self, email: str) -> dict[str, Any]:
        return await self._request(
            "POST", "/api/v1/auth/resend-verification", json={"email": email},
        )

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        return await self._request(
            "POST", "/api/v1/auth/refresh", json={"refresh_token": refresh_token},
        )

    async def logout(self, refresh_token: str, access_token: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/v1/auth/logout",
            json={"refresh_token": refresh_token},
            headers={"Authorization": f"Bearer {access_token}"},
        )

    async def internal_get_user(self, user_id: UUID | str) -> dict[str, Any] | None:
        try:
            return await self._internal_get(f"/api/v1/internal/users/{user_id}")
        except IdentityClientError as exc:
            if exc.status_code == 404:
                return None
            raise

    async def internal_upsert_by_legacy(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._internal_post("/api/v1/internal/users/upsert-by-legacy", body)

    # ── helpers ────────────────────────────────────────────────────

    async def _internal_get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        token = mint_service_token("scienthesis-identity")
        return await self._request(
            "GET", path, params=params, headers={"X-Service-Token": token},
        )

    async def _internal_post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        token = mint_service_token("scienthesis-identity")
        return await self._request(
            "POST", path, json=body, headers={"X-Service-Token": token},
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._require_base()}{path}"
        try:
            resp = await self._client.request(
                method, url, json=json, params=params, headers=headers,
            )
        except httpx.HTTPError as exc:
            logger.error(
                "identity_request_transport_failed",
                path=path, method=method, error=str(exc),
            )
            raise IdentityUpstreamError(
                f"Could not reach Identity at {url}: {exc}",
            ) from exc

        if resp.status_code >= 500:
            raise IdentityUpstreamError(
                f"Identity returned {resp.status_code}: {resp.text[:200]}",
            )
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except ValueError:
                detail = resp.text
            raise IdentityClientError(status_code=resp.status_code, detail=detail)
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()
