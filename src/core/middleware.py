"""Request tracing, logging middleware, and exception handler bindings."""

from time import perf_counter
from uuid import uuid4

import structlog
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.core.exceptions import (
    AuthenticationError,
    DeduplicationError,
    EnrichmentError,
    LitBridgeError,
    OTPError,
    RateLimitError,
    SearchNotFoundError,
    SourceFetchError,
)

logger = structlog.get_logger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a unique request identifier to request and response."""

    async def dispatch(self, request: Request, call_next) -> JSONResponse:
        request_id = str(uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class StructuredLoggingMiddleware(BaseHTTPMiddleware):
    """Emit request logs with duration and status metadata."""

    async def dispatch(self, request: Request, call_next) -> JSONResponse:
        started_at = perf_counter()
        response = await call_next(request)
        duration_ms = (perf_counter() - started_at) * 1000
        request_id = getattr(request.state, "request_id", None)

        logger.info(
            "http_request",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round(duration_ms, 2),
            request_id=request_id,
        )
        return response


async def domain_exception_handler(request: Request, exc: LitBridgeError) -> JSONResponse:
    """Map domain exceptions into consistent JSON HTTP responses."""

    request_id = getattr(request.state, "request_id", None)

    if isinstance(exc, SourceFetchError):
        status_code = 502
        payload: dict[str, str | int | float | None] = {
            "detail": exc.message,
            "source": exc.source,
            "status_code": exc.status_code,
            "request_id": request_id,
        }
    elif isinstance(exc, SearchNotFoundError):
        status_code = 404
        payload = {
            "detail": exc.message,
            "search_id": exc.search_id,
            "request_id": request_id,
        }
    elif isinstance(exc, RateLimitError):
        status_code = 429
        payload = {
            "detail": exc.message,
            "source": exc.source,
            "retry_after": exc.retry_after,
            "request_id": request_id,
        }
    elif isinstance(exc, AuthenticationError):
        status_code = 401
        payload = {"detail": exc.message, "request_id": request_id}
    elif isinstance(exc, OTPError):
        status_code = 400
        payload = {"detail": exc.message, "request_id": request_id}
    elif isinstance(exc, (DeduplicationError, EnrichmentError)):
        status_code = 500
        payload = {"detail": exc.message, "request_id": request_id}
    else:
        status_code = 500
        payload = {"detail": exc.message, "request_id": request_id}

    return JSONResponse(status_code=status_code, content=payload)
