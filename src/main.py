"""FastAPI application entrypoint for LitBridge."""

from contextlib import asynccontextmanager
from typing import Any

import httpx
import redis.asyncio as redis
import structlog
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.v1.chat import router as chat_router
from src.api.v1.enrichment import router as enrichment_router
from src.api.v1.prisma import router as prisma_router
from src.api.v1.search import router as search_router
from src.core.config import get_settings
from src.core.database import create_engine, create_session_factory
from src.core.deps import get_db, get_redis
from src.core.exceptions import LitBridgeError
from src.core.middleware import (
    RequestIDMiddleware,
    StructuredLoggingMiddleware,
    domain_exception_handler,
)
from src.core.redis import create_redis_pool

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize and tear down shared infrastructure clients."""

    engine = create_engine()
    db_session_factory = create_session_factory(engine)
    redis_client = create_redis_pool()
    http_client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))

    app.state.engine = engine
    app.state.db_session_factory = db_session_factory
    app.state.redis = redis_client
    app.state.http_client = http_client
    logger.info("startup_complete")

    try:
        yield
    finally:
        await http_client.aclose()
        await redis_client.aclose()
        await engine.dispose()
        logger.info("shutdown_complete")


app = FastAPI(title="LitBridge API", version="1.0.0", lifespan=lifespan)
settings = get_settings()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(StructuredLoggingMiddleware)
app.add_exception_handler(LitBridgeError, domain_exception_handler)

app.include_router(search_router, prefix="/api/v1")
app.include_router(enrichment_router, prefix="/api/v1")
app.include_router(prisma_router, prefix="/api/v1")
app.include_router(chat_router, prefix="/api/v1")


@app.get("/")
async def root() -> dict[str, str]:
    """Return basic API metadata."""

    return {"name": "LitBridge API", "version": "1.0.0", "status": "ok"}


@app.get("/health")
async def health_check(
    db: AsyncSession = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),
) -> dict[str, Any]:
    """Return health status after probing database and Redis."""

    database_status = "connected"
    redis_status = "connected"

    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        database_status = "unavailable"

    try:
        await redis_client.ping()
    except Exception:
        redis_status = "unavailable"

    return {"status": "ok", "database": database_status, "redis": redis_status}
