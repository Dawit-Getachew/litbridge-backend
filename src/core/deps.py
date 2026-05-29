"""Dependency providers for database, cache, HTTP, and settings."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from uuid import UUID

import httpx
import jwt
import redis.asyncio as redis
import structlog
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.llm_client import LLMClient
from src.core.config import Settings, get_settings as get_cached_settings
from src.core.database import get_db_session
from src.core.exceptions import AuthenticationError
from src.core.redis import get_redis as get_redis_client
from src.core.security import decode_access_token
from src.models.user import User
from src.repositories.conversation_repo import ConversationRepository
from src.repositories.europepmc_repo import EuropePMCRepository
from src.repositories.openalex_repo import OpenAlexRepository
from src.repositories.semantic_scholar_repo import SemanticScholarRepository
from src.repositories.library_repo import LibraryRepository
from src.repositories.research_collection_repo import ResearchCollectionRepository
from src.repositories.search_repo import SearchRepository
from src.repositories.unpaywall_repo import UnpaywallRepository
from src.repositories.user_repo import UserRepository
from src.services import FetcherService
from src.services.auth_service import AuthService
from src.services.chat_service import ChatService
from src.services.dedup_service import DedupService
from src.services.email_service import EmailService
from src.services.enrichment_service import EnrichmentService
from src.services.oa_service import OAService
from src.services.paper_extraction_service import PaperExtractionService
from src.services.prisma_service import PrismaService
from src.services.library_service import LibraryService
from src.services.research_collection_service import ResearchCollectionService
from src.services.search_service import SearchService
from src.services.streaming_search_service import StreamingSearchService

logger = structlog.get_logger(__name__)

oauth2_scheme = HTTPBearer(auto_error=True)
oauth2_scheme_optional = HTTPBearer(auto_error=False)


def get_settings() -> Settings:
    """Return cached application settings."""

    return get_cached_settings()


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """Yield a database session from the application session factory."""

    session_factory = request.app.state.db_session_factory
    async for session in get_db_session(session_factory):
        yield session


def get_redis(_request: Request) -> redis.Redis:
    """Return the shared Redis client."""

    return get_redis_client()


def get_http_client(request: Request) -> httpx.AsyncClient:
    """Return the shared HTTP client from app state."""

    return request.app.state.http_client


def get_fetcher_service(request: Request) -> FetcherService:
    """Return the federated fetch orchestrator service."""

    return FetcherService(
        client=get_http_client(request),
        redis_client=get_redis(request),
        settings=get_settings(),
        llm_client=get_llm_client(request),
    )


def get_dedup_service() -> DedupService:
    """Return a deduplication service instance."""

    return DedupService()


def get_prisma_service() -> PrismaService:
    """Return a PRISMA computation service instance."""

    return PrismaService()


async def get_search_repo(db: AsyncSession = Depends(get_db)) -> SearchRepository:
    """Return search session repository bound to current DB session."""

    return SearchRepository(db=db)


def get_semantic_scholar_repo(request: Request) -> SemanticScholarRepository:
    """Return Semantic Scholar enrichment repository instance."""

    return SemanticScholarRepository(
        client=get_http_client(request),
        redis_client=get_redis(request),
        settings=get_settings(),
    )


def get_openalex_repo(request: Request) -> OpenAlexRepository:
    """Return OpenAlex repository instance."""

    return OpenAlexRepository(
        client=get_http_client(request),
        settings=get_settings(),
    )


def get_europepmc_repo(request: Request) -> EuropePMCRepository:
    """Return Europe PMC repository instance."""

    return EuropePMCRepository(
        client=get_http_client(request),
        settings=get_settings(),
    )


def get_unpaywall_repo(request: Request) -> UnpaywallRepository:
    """Return Unpaywall repository instance."""

    return UnpaywallRepository(
        client=get_http_client(request),
        redis_client=get_redis(request),
        settings=get_settings(),
    )


def get_llm_client(request: Request) -> LLMClient:
    """Return LLM client backed by the app's shared httpx client."""

    http_client: httpx.AsyncClient | None = getattr(request.app.state, "http_client", None)
    return LLMClient(settings=get_settings(), client=http_client)


def get_enrichment_service(request: Request) -> EnrichmentService:
    """Return enrichment orchestrator service."""

    return EnrichmentService(
        s2_repo=get_semantic_scholar_repo(request),
        llm_client=get_llm_client(request),
        redis_client=get_redis(request),
    )


def get_oa_service(request: Request) -> OAService:
    """Return open-access resolution service."""

    return OAService(
        openalex_repo=get_openalex_repo(request),
        unpaywall_repo=get_unpaywall_repo(request),
        europepmc_repo=get_europepmc_repo(request),
        redis_client=get_redis(request),
    )


async def get_search_service(
    request: Request,
    search_repo: SearchRepository = Depends(get_search_repo),
) -> SearchService:
    """Return the orchestrator for search execution and retrieval."""

    return SearchService(
        fetcher=get_fetcher_service(request),
        dedup=get_dedup_service(),
        prisma=get_prisma_service(),
        search_repo=search_repo,
        redis_client=get_redis(request),
        enrichment_service=get_enrichment_service(request),
        oa_service=get_oa_service(request),
        llm_client=get_llm_client(request),
    )


async def get_streaming_search_service(
    request: Request,
    search_repo: SearchRepository = Depends(get_search_repo),
) -> StreamingSearchService:
    """Return the orchestrator for streaming search execution."""

    return StreamingSearchService(
        fetcher=get_fetcher_service(request),
        dedup=get_dedup_service(),
        prisma=get_prisma_service(),
        search_repo=search_repo,
        redis_client=get_redis(request),
        enrichment_service=get_enrichment_service(request),
        oa_service=get_oa_service(request),
        llm_client=get_llm_client(request),
    )


async def get_conversation_repo(
    db: AsyncSession = Depends(get_db),
) -> ConversationRepository:
    """Return conversation repository bound to current DB session."""

    return ConversationRepository(db=db)


async def get_chat_service(
    request: Request,
    conversation_repo: ConversationRepository = Depends(get_conversation_repo),
) -> ChatService:
    """Return the conversational chat service."""

    settings = get_settings()
    return ChatService(
        conversation_repo=conversation_repo,
        llm_client=get_llm_client(request),
        max_history_turns=settings.CHAT_MAX_HISTORY_TURNS,
        max_context_records=settings.CHAT_MAX_CONTEXT_RECORDS,
    )


# ── Library dependencies ─────────────────────────────────────────

async def get_library_repo(db: AsyncSession = Depends(get_db)) -> LibraryRepository:
    """Return library repository bound to current DB session."""

    return LibraryRepository(db=db)


async def get_library_service(
    library_repo: LibraryRepository = Depends(get_library_repo),
    search_repo: SearchRepository = Depends(get_search_repo),
) -> LibraryService:
    """Return the library/collections service."""

    return LibraryService(library_repo=library_repo, search_repo=search_repo)


# ── Research Collection dependencies ─────────────────────────────

async def get_research_collection_repo(
    db: AsyncSession = Depends(get_db),
) -> ResearchCollectionRepository:
    """Return research collection repository bound to current DB session."""

    return ResearchCollectionRepository(db=db)


async def get_research_collection_service(
    request: Request,
    repo: ResearchCollectionRepository = Depends(get_research_collection_repo),
) -> ResearchCollectionService:
    """Return the research collections service.

    Injects a LitHub client when configured so collection listings can enrich
    item metadata from the central library (best-effort, gated).
    """
    settings = get_settings()
    lithub_client = None
    lithub_enabled = bool(settings.LITHUB_BASE_URL) and settings.LITPORTAL_DUAL_WRITE_LITHUB
    if lithub_enabled:
        lithub_client = get_lithub_client(request)
    return ResearchCollectionService(
        repo=repo,
        lithub_client=lithub_client,
        lithub_enabled=lithub_enabled,
    )


async def get_paper_extraction_service(
    request: Request,
    repo: ResearchCollectionRepository = Depends(get_research_collection_repo),
    search_repo: SearchRepository = Depends(get_search_repo),
) -> PaperExtractionService:
    """Return the AI paper metadata extraction service."""

    return PaperExtractionService(
        llm_client=get_llm_client(request),
        redis_client=get_redis(request),
        repo=repo,
        search_repo=search_repo,
    )


# ── Scienthesis platform clients ─────────────────────────────────


def get_lithub_client(request: Request) -> "LitHubClient":
    """Return a request-scoped LitHub HTTP client."""
    from src.clients.lithub_client import LitHubClient

    return LitHubClient(http_client=get_http_client(request), settings=get_settings())


def get_identity_client(request: Request) -> "IdentityClient":
    """Return a request-scoped Identity HTTP client."""
    from src.clients.identity_client import IdentityClient

    return IdentityClient(http_client=get_http_client(request), settings=get_settings())


async def get_lithub_sync_repo(
    db: AsyncSession = Depends(get_db),
) -> "LitHubSyncRepository":
    from src.repositories.lithub_sync_repo import LitHubSyncRepository

    return LitHubSyncRepository(db=db)


async def get_lithub_sync_service(
    request: Request,
    outbox: "LitHubSyncRepository" = Depends(get_lithub_sync_repo),
) -> "LitHubSyncService":
    from src.services.lithub_sync_service import LitHubSyncService

    return LitHubSyncService(lithub=get_lithub_client(request), outbox=outbox)


# ── Auth dependencies ────────────────────────────────────────────

async def get_user_repo(db: AsyncSession = Depends(get_db)) -> UserRepository:
    """Return user repository bound to the current DB session."""

    return UserRepository(db=db)


def get_email_service(request: Request) -> EmailService:
    """Return the email delivery service."""

    return EmailService(
        http_client=get_http_client(request),
        settings=get_settings(),
    )


async def get_auth_service(
    request: Request,
    user_repo: UserRepository = Depends(get_user_repo),
) -> AuthService:
    """Return the authentication orchestrator.

    Injects an IdentityClient when ``LITPORTAL_USE_IDENTITY`` is enabled so the
    OTP/refresh/logout flows delegate to the Scienthesis Identity Service.
    """
    settings = get_settings()
    identity_client = None
    if settings.LITPORTAL_USE_IDENTITY and settings.IDENTITY_BASE_URL:
        identity_client = get_identity_client(request)

    return AuthService(
        user_repo=user_repo,
        email_service=get_email_service(request),
        redis_client=get_redis(request),
        settings=settings,
        identity_client=identity_client,
    )


async def _resolve_litpulse_token(
    raw_token: str,
    user_repo: UserRepository,
    settings: Settings,
) -> User | None:
    """Try to validate `raw_token` as a LitPulse-issued JWT.

    Returns the upserted Portal-Engine user on success, or None when the token
    is not recognized as a LitPulse token (so the caller can decide between
    "fall through to next validator" and "raise 401").
    """
    if not settings.LITPULSE_JWT_ENABLED or not settings.LITPULSE_JWT_SECRET_KEY:
        return None
    try:
        payload = jwt.decode(
            raw_token,
            settings.LITPULSE_JWT_SECRET_KEY,
            algorithms=["HS256"],
        )
    except jwt.InvalidTokenError:
        return None

    if payload.get("type") != "access":
        return None

    litpulse_user_id = payload.get("user_id")
    email = payload.get("email")
    if not litpulse_user_id or not email:
        # A valid signature but a payload that doesn't match the documented
        # LitPulse contract — log and treat as auth failure to surface the bug.
        logger.warning(
            "litpulse_token_missing_claims",
            has_user_id=bool(litpulse_user_id),
            has_email=bool(email),
        )
        raise AuthenticationError("LitPulse token missing required claims.")

    user = await user_repo.upsert_litpulse_user(
        litpulse_user_id=str(litpulse_user_id),
        email=str(email),
    )
    return user if user.is_active else None


async def _resolve_identity_token(
    raw_token: str,
    user_repo: UserRepository,
    settings: Settings,
    http_client: httpx.AsyncClient,
) -> User | None:
    """Try to validate `raw_token` as a Scienthesis Identity-issued JWT.

    Returns the upserted shadow `User` on success, ``None`` when the token
    isn't Identity-shaped (so the caller can fall through to the next path).
    Raises :class:`AuthenticationError` for tokens that ARE Identity-shaped
    but invalid (expired, signature mismatch) — we never silently downgrade
    a valid-but-failed Identity token to another validator.
    """
    if not settings.LITPORTAL_USE_IDENTITY or not settings.IDENTITY_BASE_URL:
        return None
    from src.clients.identity_client import validate_identity_access_token

    try:
        payload = await validate_identity_access_token(
            raw_token, http_client=http_client, settings=settings,
        )
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError(f"Invalid Identity access token: {exc}") from exc
    if payload is None:
        return None

    sub = payload.get("sub")
    email = payload.get("email")
    if not sub:
        raise AuthenticationError("Identity token missing 'sub' claim.")
    try:
        identity_uuid = UUID(str(sub))
    except (TypeError, ValueError) as exc:
        raise AuthenticationError("Identity token 'sub' is not a UUID.") from exc

    user = await user_repo.upsert_identity_user(
        identity_id=identity_uuid,
        email=str(email or ""),
    )
    return user if user.is_active else None


async def get_current_user(
    request: Request = None,  # noqa: RUF013 — FastAPI injects Request; the default lets unit tests omit it
    token: HTTPAuthorizationCredentials = Depends(oauth2_scheme),
    user_repo: UserRepository = Depends(get_user_repo),
) -> User:
    """Decode JWT and load the authenticated user, or raise 401.

    Validator paths, tried in order:
      1. Native Portal Engine OTP-issued token (HS256, secret = ``SECRET_KEY``).
      2. Identity Service-issued token (RS256, validated via JWKS) — Phase 2.
      3. LitPulse-issued legacy bridge token (HS256, secret =
         ``LITPULSE_JWT_SECRET_KEY``) — Phase 1, kept during cutover.

    Each token format is unambiguous by signature/algorithm. We try native
    first because it's cheapest; the Identity path is consulted next because
    Phase 2 is the going-forward standard; the LitPulse legacy bridge is the
    fallback so existing sessions keep working until they expire naturally.

    ``request`` is optional so the dependency can also be called directly in
    unit tests; the Identity path needs the app's shared httpx client and is
    skipped when no request context is available.
    """
    raw_token = token.credentials
    settings = get_settings()
    http_client: httpx.AsyncClient | None = (
        request.app.state.http_client if request is not None else None
    )

    # 1. Native Portal Engine token.
    try:
        payload = decode_access_token(raw_token)
        user_id = payload.get("sub")
        if user_id is None:
            raise AuthenticationError("Malformed token payload.")
        user = await user_repo.get_by_id(UUID(user_id))
        if user is None or not user.is_active:
            raise AuthenticationError("User not found or deactivated.")
        return user
    except jwt.InvalidTokenError:
        pass  # Try the next validator.

    # 2. Identity Service token (Phase 2). Needs the shared HTTP client for JWKS.
    if http_client is not None:
        identity_user = await _resolve_identity_token(
            raw_token, user_repo, settings, http_client,
        )
        if identity_user is not None:
            return identity_user

    # 3. LitPulse legacy bridge token (Phase 1).
    litpulse_user = await _resolve_litpulse_token(raw_token, user_repo, settings)
    if litpulse_user is not None:
        return litpulse_user

    raise AuthenticationError("Invalid or expired access token.")


async def get_current_user_optional(
    request: Request,
    token: HTTPAuthorizationCredentials | None = Depends(oauth2_scheme_optional),
    user_repo: UserRepository = Depends(get_user_repo),
) -> User | None:
    """Like ``get_current_user`` but returns ``None`` when no token is present."""

    if token is None:
        return None
    try:
        return await get_current_user(
            request=request, token=token, user_repo=user_repo,
        )
    except AuthenticationError as exc:
        logger.warning("optional_auth_failed", reason=exc.message)
        return None
