"""Authentication endpoints (email OTP, token refresh, logout)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from src.core.deps import get_auth_service, get_current_user
from src.models.user import User
from src.schemas.auth import (
    MessageResponse,
    OTPRequest,
    OTPVerify,
    RefreshRequest,
    TokenResponse,
    UserResponse,
)
from src.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/request-otp", response_model=MessageResponse)
async def request_otp(
    body: OTPRequest,
    auth: AuthService = Depends(get_auth_service),
) -> MessageResponse:
    """Send a one-time verification code to the provided email."""
    await auth.request_otp(body.email)
    return MessageResponse(message="Verification code sent. Check your email.")


@router.post("/verify-otp", response_model=TokenResponse)
async def verify_otp(
    body: OTPVerify,
    auth: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    """Verify the OTP code and return access + refresh tokens."""
    return await auth.verify_otp(body.email, body.code)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    body: RefreshRequest,
    auth: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    """Exchange a refresh token for a new token pair."""
    return await auth.refresh_tokens(body.refresh_token)


@router.post("/logout", response_model=MessageResponse)
async def logout(
    body: RefreshRequest,
    _user: User = Depends(get_current_user),
    auth: AuthService = Depends(get_auth_service),
) -> MessageResponse:
    """Revoke the provided refresh token."""
    await auth.logout(body.refresh_token)
    return MessageResponse(message="Logged out successfully.")


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)) -> UserResponse:
    """Return the authenticated user's profile."""
    return UserResponse.model_validate(user)
