"""Authentication endpoints (email OTP, token refresh, logout)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials

from src.core.deps import get_auth_service, get_current_user, oauth2_scheme
from src.models.user import User
from src.schemas.auth import (
    LoginRequest,
    MessageResponse,
    OTPRequest,
    OTPVerify,
    RefreshRequest,
    ResendVerificationRequest,
    SignupRequest,
    TokenResponse,
    UserResponse,
    VerifyCodeRequest,
)
from src.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    auth: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    """Email + password login, delegated to the Identity Service.

    The returned token also works on LitPulse — it's the same Identity account.
    """
    from fastapi import HTTPException

    from src.clients.identity_client import IdentityClientError, IdentityUpstreamError

    try:
        return await auth.login(body.email, body.password)
    except IdentityUpstreamError as exc:
        raise HTTPException(status_code=503, detail="Identity service unavailable") from exc
    except IdentityClientError as exc:
        raise HTTPException(status_code=exc.status_code or 401, detail=exc.detail) from exc


@router.post("/signup", response_model=TokenResponse, status_code=201)
async def signup(
    body: SignupRequest,
    auth: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    """Email + password signup, delegated to the Identity Service."""
    from fastapi import HTTPException

    from src.clients.identity_client import IdentityClientError, IdentityUpstreamError

    try:
        return await auth.signup(body.email, body.password, body.full_name)
    except IdentityUpstreamError as exc:
        raise HTTPException(status_code=503, detail="Identity service unavailable") from exc
    except IdentityClientError as exc:
        raise HTTPException(status_code=exc.status_code or 400, detail=exc.detail) from exc


@router.post("/verify-code", response_model=MessageResponse)
async def verify_code(
    body: VerifyCodeRequest,
    auth: AuthService = Depends(get_auth_service),
) -> MessageResponse:
    """Verify a password-signup email with the 6-digit code Identity emailed."""
    from fastapi import HTTPException

    from src.clients.identity_client import IdentityClientError, IdentityUpstreamError

    try:
        await auth.verify_email_code(body.email, body.code)
    except IdentityUpstreamError as exc:
        raise HTTPException(status_code=503, detail="Identity service unavailable") from exc
    except IdentityClientError as exc:
        raise HTTPException(status_code=exc.status_code or 400, detail=exc.detail) from exc
    return MessageResponse(message="Email verified.")


@router.post("/resend-verification", response_model=MessageResponse)
async def resend_verification(
    body: ResendVerificationRequest,
    auth: AuthService = Depends(get_auth_service),
) -> MessageResponse:
    """Re-send the 6-digit email verification code."""
    await auth.resend_verification(body.email)
    return MessageResponse(message="If an account exists, a verification code has been sent.")


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
    creds: HTTPAuthorizationCredentials = Depends(oauth2_scheme),
    auth: AuthService = Depends(get_auth_service),
) -> MessageResponse:
    """Revoke the provided refresh token."""
    await auth.logout(body.refresh_token, access_token=creds.credentials)
    return MessageResponse(message="Logged out successfully.")


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)) -> UserResponse:
    """Return the authenticated user's profile."""
    return UserResponse.model_validate(user)
