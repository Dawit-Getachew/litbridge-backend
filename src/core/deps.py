"""Dependency providers for database, cache, HTTP, and settings."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from uuid import UUID

import httpx
import jwt
import redis.asyncio as redis
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
    repo: ResearchCollectionRepository = Depends(get_research_collection_repo),
) -> ResearchCollectionService:
    """Return the research collections service."""

    return ResearchCollectionService(repo=repo)


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
    """Return the authentication orchestrator."""

    return AuthService(
        user_repo=user_repo,
        email_service=get_email_service(request),
        redis_client=get_redis(request),
        settings=get_settings(),
    )


async def get_current_user(
    token: HTTPAuthorizationCredentials,
    user_repo: UserRepository = Depends(get_user_repo),
) -> User:
    """Decode JWT and load the authenticated user, or raise 401."""

    try:
        payload = decode_access_token(token.credentials)
    except jwt.InvalidTokenError:
        raise AuthenticationError("Invalid or expired access token.")

    user_id = payload.get("sub")
    if user_id is None:
        raise AuthenticationError("Malformed token payload.")

    user = await user_repo.get_by_id(UUID(user_id))
    if user is None or not user.is_active:
        raise AuthenticationError("User not found or deactivated.")
    return user


async def get_current_user_optional(
    token: HTTPAuthorizationCredentials | None = Depends(oauth2_scheme_optional),
    user_repo: UserRepository = Depends(get_user_repo),
) -> User | None:
    """Like ``get_current_user`` but returns ``None`` when no token is present."""

    if token is None:
        return None
    try:
        return await get_current_user(token=token, user_repo=user_repo)
    except AuthenticationError:
        return None
