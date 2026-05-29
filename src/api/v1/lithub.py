"""LitPortal view of the central LitHub library.

Surfaces the user's cross-app saved papers (saved from LitPulse OR from a
LitPortal collection) in one unified list, keyed by the user's Identity ``sub``.
This is what makes a paper saved in LitPulse show up in LitPortal — the
symmetric counterpart of LitPulse's GET /api/library reading LitHub.

The list is read-only here; saves still happen through the existing surfaces
(collection add → mirror to LitHub). Returns the canonical LitHub article shape
(pmid, doi, title, journal, authors, abstract, ai_summary, study_design,
design_tags, saved_at, folder, …) so the frontend can render + sort it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Depends, Query, Request

from src.core.config import get_settings
from src.core.deps import get_current_user, get_lithub_client
from src.models.user import User

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/lithub", tags=["LitHub"])

SortBy = Literal["saved_at", "title", "journal"]
SortDir = Literal["asc", "desc"]

_EMPTY = {"articles": [], "total": 0, "next_cursor": None}


@router.get("/library")
async def get_lithub_library(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
    search: str | None = None,
    design_type: str | None = None,
    saved_after: datetime | None = None,
    sort_by: SortBy = "saved_at",
    sort_dir: SortDir = "desc",
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Return the user's unified central library (papers saved from any app).

    Empty when LitHub isn't configured or the user has no Identity link yet —
    never errors the page.
    """
    settings = get_settings()
    if not settings.LITHUB_BASE_URL:
        return dict(_EMPTY)

    identity_sub = getattr(user, "identity_id", None)
    if identity_sub is None:
        # User hasn't been provisioned into Identity yet → no central library.
        return dict(_EMPTY)

    client = get_lithub_client(request)
    try:
        result = await client.internal_list_library(
            identity_sub,
            params={
                "limit": limit,
                "cursor": cursor,
                "search": search,
                "design_type": design_type,
                "saved_after": saved_after.isoformat() if saved_after else None,
                "sort_by": sort_by,
                "sort_dir": sort_dir,
            },
        )
    except Exception as exc:  # noqa: BLE001 — never break the page on a LitHub hiccup
        logger.warning("lithub_library_read_failed", user_id=str(user.id), error=str(exc))
        return dict(_EMPTY)

    return {
        "articles": result.get("articles", []),
        "total": result.get("total", 0),
        "next_cursor": result.get("next_cursor"),
    }
