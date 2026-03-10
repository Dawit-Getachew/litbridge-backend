"""Request / response schemas for the authentication API."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


# ── Requests ─────────────────────────────────────────────────────

class OTPRequest(BaseModel):
    """Body for POST /auth/request-otp."""

    email: EmailStr


class OTPVerify(BaseModel):
    """Body for POST /auth/verify-otp."""

    email: EmailStr
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class RefreshRequest(BaseModel):
    """Body for POST /auth/refresh."""

    refresh_token: str


# ── Responses ────────────────────────────────────────────────────

class TokenResponse(BaseModel):
    """Returned after successful OTP verification or token refresh."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class UserResponse(BaseModel):
    """Public representation of a user."""

    id: UUID
    email: str
    display_name: str | None
    is_verified: bool
    auth_provider: str
    created_at: datetime

    model_config = {"from_attributes": True}


class MessageResponse(BaseModel):
    """Generic status message."""

    message: str
