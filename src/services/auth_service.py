"""Core authentication business logic (provider-agnostic token layer)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

import redis.asyncio as redis
import structlog

from src.core.config import Settings
from src.core.exceptions import AuthenticationError, OTPError
from src.core.security import (
    create_access_token,
    create_refresh_token,
    generate_otp,
    hash_token,
)
from src.repositories.user_repo import UserRepository
from src.schemas.auth import TokenResponse
from src.services.email_service import EmailService

if TYPE_CHECKING:
    from src.clients.identity_client import IdentityClient

logger = structlog.get_logger(__name__)


class AuthService:
    """Orchestrates OTP verification, token issuance, and refresh flows."""

    def __init__(
        self,
        user_repo: UserRepository,
        email_service: EmailService,
        redis_client: redis.Redis,
        settings: Settings,
        identity_client: "IdentityClient | None" = None,
    ) -> None:
        self._repo = user_repo
        self._email = email_service
        self._redis = redis_client
        self._settings = settings
        self._identity = identity_client

    @property
    def _identity_enabled(self) -> bool:
        """True when auth should be delegated to the Scienthesis Identity Service."""
        return bool(
            self._settings.LITPORTAL_USE_IDENTITY
            and self._settings.IDENTITY_BASE_URL
            and self._identity is not None,
        )

    # ── Email + password flow (delegated to Identity) ───────────

    async def login(self, email: str, password: str) -> TokenResponse:
        """Authenticate email + password via the Identity Service.

        The SAME Identity credentials work on LitPulse and LitPortal; the
        returned RS256 token validates on both. Password auth is only
        available when Identity delegation is enabled (LitPortal has no local
        password store of its own).
        """
        if not self._identity_enabled:
            raise AuthenticationError(
                "Password sign-in is unavailable (Identity Service not enabled).",
            )
        data = await self._identity.login(email, password)
        return TokenResponse(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            token_type=data.get("token_type", "bearer"),
            expires_in=data.get("expires_in", self._settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60),
        )

    async def signup(self, email: str, password: str, full_name: str | None) -> TokenResponse:
        """Create an account (email + password) via the Identity Service.

        Mirrors LitPulse signup: the account lives in Identity, so the same
        credentials immediately work on both apps. The local shadow ``User``
        row is created lazily on the first authenticated request.
        """
        if not self._identity_enabled:
            raise AuthenticationError(
                "Password sign-up is unavailable (Identity Service not enabled).",
            )
        data = await self._identity.signup({
            "email": email,
            "password": password,
            "signup_method": "password",
            "full_name": full_name,
        })
        return TokenResponse(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            token_type=data.get("token_type", "bearer"),
            expires_in=data.get("expires_in", self._settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60),
        )

    # ── Email verification (6-digit code, delegated to Identity) ──

    async def verify_email_code(self, email: str, code: str) -> None:
        """Confirm a password-signup email via the 6-digit code Identity emailed."""
        if not self._identity_enabled:
            raise AuthenticationError(
                "Email verification is unavailable (Identity Service not enabled).",
            )
        await self._identity.verify_email_code(email, code)

    async def resend_verification(self, email: str) -> None:
        """Re-send the email verification code (best-effort; never leaks existence)."""
        if not self._identity_enabled:
            return
        await self._identity.resend_verification(email)

    # ── OTP flow ─────────────────────────────────────────────────

    async def request_otp(self, email: str) -> None:
        """Generate an OTP, store it in Redis, and email it to the user.

        When Identity delegation is enabled, the OTP is issued and emailed by
        the Scienthesis Identity Service (single source of truth for users).
        """
        if self._identity_enabled:
            await self._identity.request_otp(email)
            logger.info("otp_requested_via_identity", email=email)
            return

        rate_key = f"otp_rate:{email}"
        sends = await self._redis.get(rate_key)
        if sends is not None and int(sends) >= 5:
            raise OTPError("Too many OTP requests. Please try again later.")

        code = generate_otp()
        otp_payload = json.dumps({"code": code, "attempts": 0})
        await self._redis.setex(
            f"otp:{email}",
            self._settings.OTP_EXPIRE_SECONDS,
            otp_payload,
        )

        pipe = self._redis.pipeline()
        pipe.incr(rate_key)
        pipe.expire(rate_key, 3600)
        await pipe.execute()

        expire_minutes = self._settings.OTP_EXPIRE_SECONDS // 60
        await self._email.send_otp_email(email, code, expire_minutes)
        logger.info("otp_requested", email=email)

    async def verify_otp(self, email: str, code: str) -> TokenResponse:
        """Validate an OTP code and return access + refresh tokens.

        When Identity delegation is enabled, verification + token issuance is
        performed by the Identity Service and its RS256 token pair is returned
        verbatim (same ``TokenResponse`` shape). The local shadow ``User`` row
        is created lazily on the next authenticated request via
        ``get_current_user`` → ``upsert_identity_user``.
        """
        if self._identity_enabled:
            data = await self._identity.verify_otp(email, code)
            return TokenResponse(
                access_token=data["access_token"],
                refresh_token=data["refresh_token"],
                token_type=data.get("token_type", "bearer"),
                expires_in=data.get(
                    "expires_in", self._settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
                ),
            )

        otp_key = f"otp:{email}"
        raw = await self._redis.get(otp_key)
        if raw is None:
            raise OTPError("Verification code has expired. Please request a new one.")

        data = json.loads(raw)
        attempts = data.get("attempts", 0)

        if attempts >= self._settings.OTP_MAX_ATTEMPTS:
            await self._redis.delete(otp_key)
            raise OTPError("Too many failed attempts. Please request a new code.")

        if data["code"] != code:
            data["attempts"] = attempts + 1
            ttl = await self._redis.ttl(otp_key)
            if ttl > 0:
                await self._redis.setex(otp_key, ttl, json.dumps(data))
            remaining = self._settings.OTP_MAX_ATTEMPTS - data["attempts"]
            raise OTPError(f"Invalid code. {remaining} attempt(s) remaining.")

        await self._redis.delete(otp_key)

        user = await self._repo.get_by_email(email)
        if user is None:
            user = await self._repo.create(email, provider="email")
            logger.info("user_created", user_id=str(user.id), email=email)

        if not user.is_active:
            raise AuthenticationError("Account is deactivated.")

        await self._repo.update_last_login(user.id, datetime.now(UTC))
        return await self._issue_tokens(user.id, user.email, user.auth_provider)

    # ── Token lifecycle ──────────────────────────────────────────

    async def refresh_tokens(self, raw_refresh_token: str) -> TokenResponse:
        """Exchange a valid refresh token for a new token pair."""
        if self._identity_enabled:
            data = await self._identity.refresh(raw_refresh_token)
            return TokenResponse(
                access_token=data["access_token"],
                refresh_token=data["refresh_token"],
                token_type=data.get("token_type", "bearer"),
                expires_in=data.get(
                    "expires_in", self._settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
                ),
            )

        token_hash = hash_token(raw_refresh_token)
        stored = await self._repo.get_refresh_token(token_hash)

        if stored is None:
            raise AuthenticationError("Invalid refresh token.")
        if stored.expires_at.replace(tzinfo=UTC) < datetime.now(UTC):
            await self._repo.revoke_refresh_token(token_hash)
            raise AuthenticationError("Refresh token has expired.")

        user = await self._repo.get_by_id(stored.user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("Account not found or deactivated.")

        await self._repo.revoke_refresh_token(token_hash)
        return await self._issue_tokens(user.id, user.email, user.auth_provider)

    async def logout(self, raw_refresh_token: str, access_token: str | None = None) -> None:
        """Revoke the given refresh token.

        Under Identity delegation the refresh token is an Identity-issued
        opaque token, so revocation is delegated to Identity (best-effort —
        a transient Identity outage must not block logout).
        """
        if self._identity_enabled:
            try:
                await self._identity.logout(raw_refresh_token, access_token or "")
            except Exception as exc:  # noqa: BLE001  — logout is best-effort
                logger.warning("identity_logout_failed", error=str(exc))
            return

        token_hash = hash_token(raw_refresh_token)
        await self._repo.revoke_refresh_token(token_hash)
        logger.info("user_logged_out")

    # ── Helpers ──────────────────────────────────────────────────

    async def _issue_tokens(
        self, user_id: UUID, email: str, provider: str,
    ) -> TokenResponse:
        access = create_access_token(user_id, email, provider)
        raw_refresh = create_refresh_token()
        expires_at = datetime.now(UTC) + timedelta(days=self._settings.REFRESH_TOKEN_EXPIRE_DAYS)

        await self._repo.create_refresh_token(
            user_id=user_id,
            token_hash=hash_token(raw_refresh),
            expires_at=expires_at,
        )

        return TokenResponse(
            access_token=access,
            refresh_token=raw_refresh,
            token_type="bearer",
            expires_in=self._settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )
