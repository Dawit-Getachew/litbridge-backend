"""JWT token management and OTP generation utilities."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt

from src.core.config import get_settings

_ALGORITHM = "HS256"


def create_access_token(
    user_id: UUID,
    email: str,
    provider: str = "email",
    expires_delta: timedelta | None = None,
) -> str:
    """Return a signed JWT access token."""
    settings = get_settings()
    expire = datetime.now(UTC) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload = {
        "sub": str(user_id),
        "email": email,
        "provider": provider,
        "exp": expire,
        "iat": datetime.now(UTC),
        "type": "access",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode and validate an access token. Raises ``jwt.InvalidTokenError`` on failure."""
    settings = get_settings()
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[_ALGORITHM])
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("Token type is not 'access'")
    return payload


def create_refresh_token() -> str:
    """Generate a cryptographically random opaque refresh token."""
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    """Return a SHA-256 hex digest of *token* for safe DB storage."""
    return hashlib.sha256(token.encode()).hexdigest()


def generate_otp(length: int = 6) -> str:
    """Generate a cryptographically random numeric OTP code."""
    upper = 10**length
    code = secrets.randbelow(upper)
    return str(code).zfill(length)
