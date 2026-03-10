"""Core authentication business logic (provider-agnostic token layer)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
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

logger = structlog.get_logger(__name__)


class AuthService:
    """Orchestrates OTP verification, token issuance, and refresh flows."""

    def __init__(
        self,
        user_repo: UserRepository,
        email_service: EmailService,
        redis_client: redis.Redis,
        settings: Settings,
    ) -> None:
        self._repo = user_repo
        self._email = email_service
        self._redis = redis_client
        self._settings = settings

    # ── OTP flow ─────────────────────────────────────────────────

    async def request_otp(self, email: str) -> None:
        """Generate an OTP, store it in Redis, and email it to the user."""
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
        """Validate an OTP code and return access + refresh tokens."""
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

    async def logout(self, raw_refresh_token: str) -> None:
        """Revoke the given refresh token."""
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
