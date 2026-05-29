"""FastAPI router for Research Collections management (required auth)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from src.ai.llm_client import LLMClient
from src.core.config import get_settings
from src.core.deps import (
    get_current_user,
    get_llm_client,
    get_paper_extraction_service,
    get_research_collection_service,
)
from src.core.exceptions import LitBridgeError
from src.models.user import User
from src.repositories.research_collection_repo import ResearchCollectionRepository
from src.repositories.search_repo import SearchRepository
from src.schemas.research_collection import (
    AddRecordsRequest,
    CollectionDetailResponse,
    CollectionItemResponse,
    CollectionResponse,
    CollectionTreeResponse,
    CreateCollectionRequest,
    MoveRecordRequest,
    PaperMetadata,
    UpdateCollectionRequest,
)
from src.services.paper_extraction_service import PaperExtractionService
from src.services.research_collection_service import (
    SHARED_LIBRARY_COLLECTION_ID,
    CollectionAccessDeniedError,
    CollectionNestingError,
    CollectionNotFoundError,
    ResearchCollectionService,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/collections", tags=["Research Collections"])


async def _run_extraction_background(
    items: list[dict[str, Any]],
    session_factory: Any,
    llm_client: LLMClient,
    redis_client: Any,
) -> None:
    """Run paper metadata extraction in a background task with its own DB session."""
    async with session_factory() as session:
        repo = ResearchCollectionRepository(db=session)
        search_repo = SearchRepository(db=session)
        svc = PaperExtractionService(
            llm_client=llm_client,
            redis_client=redis_client,
            repo=repo,
            search_repo=search_repo,
        )
        try:
            await svc.extract_batch(items)
        except Exception as exc:
            logger.warning("background_extraction_failed", error=str(exc))


def _map_collection_error(exc: LitBridgeError) -> HTTPException:
    if isinstance(exc, CollectionNotFoundError):
        return HTTPException(status_code=404, detail=exc.message)
    if isinstance(exc, CollectionAccessDeniedError):
        return HTTPException(status_code=403, detail=exc.message)
    if isinstance(exc, CollectionNestingError):
        return HTTPException(status_code=422, detail=exc.message)
    return HTTPException(status_code=400, detail=exc.message)


# -- Collection CRUD ----------------------------------------------------------

@router.get("", response_model=CollectionTreeResponse)
async def list_collections(
    user: User = Depends(get_current_user),
    service: ResearchCollectionService = Depends(get_research_collection_service),
) -> CollectionTreeResponse:
    """Return tree of root collections with nested children and items.

    When cross-app LitHub is enabled and the user is linked to Identity, a
    read-only virtual "LitPulse Library" collection is appended so papers saved
    in LitPulse appear here too. It is omitted silently otherwise.
    """
    tree = await service.list_collections(user.id)
    shared = await service.build_shared_library_detail(getattr(user, "identity_id", None))
    if shared is not None:
        tree.collections.append(shared)
    return tree


@router.post("", response_model=CollectionResponse, status_code=201)
async def create_collection(
    payload: CreateCollectionRequest,
    user: User = Depends(get_current_user),
    service: ResearchCollectionService = Depends(get_research_collection_service),
) -> CollectionResponse:
    """Create a new research collection."""

    return await service.create_collection(user.id, payload)


@router.get("/{collection_id}", response_model=CollectionDetailResponse)
async def get_collection(
    collection_id: UUID,
    search: str | None = None,
    design_type: str | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    user: User = Depends(get_current_user),
    service: ResearchCollectionService = Depends(get_research_collection_service),
) -> CollectionDetailResponse:
    """Get a research collection with its record items.

    The reserved ``SHARED_LIBRARY_COLLECTION_ID`` is served read-through from
    the central LitHub library rather than from Postgres.
    """
    if collection_id == SHARED_LIBRARY_COLLECTION_ID:
        shared = await service.build_shared_library_detail(
            getattr(user, "identity_id", None),
            search=search,
            design_type=design_type,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )
        if shared is None:
            raise HTTPException(status_code=404, detail="Shared library is not available")
        return shared

    try:
        return await service.get_collection(collection_id, user.id)
    except LitBridgeError as exc:
        raise _map_collection_error(exc) from exc


@router.patch("/{collection_id}", response_model=CollectionResponse)
async def update_collection(
    collection_id: UUID,
    payload: UpdateCollectionRequest,
    user: User = Depends(get_current_user),
    service: ResearchCollectionService = Depends(get_research_collection_service),
) -> CollectionResponse:
    """Update a research collection's name, description, icon, color, or position."""

    try:
        return await service.update_collection(collection_id, user.id, payload)
    except LitBridgeError as exc:
        raise _map_collection_error(exc) from exc


@router.delete("/{collection_id}", status_code=204)
async def delete_collection(
    collection_id: UUID,
    user: User = Depends(get_current_user),
    service: ResearchCollectionService = Depends(get_research_collection_service),
) -> None:
    """Delete a research collection and all its items."""

    try:
        await service.delete_collection(collection_id, user.id)
    except LitBridgeError as exc:
        raise _map_collection_error(exc) from exc


# -- Record items -------------------------------------------------------------

@router.post(
    "/{collection_id}/records",
    response_model=list[CollectionItemResponse],
    status_code=201,
)
async def add_records(
    collection_id: UUID,
    payload: AddRecordsRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    service: ResearchCollectionService = Depends(get_research_collection_service),
    llm: LLMClient = Depends(get_llm_client),
) -> list[CollectionItemResponse]:
    """Add one or more records to a research collection.

    AI metadata extraction runs in the background with its own DB session. When
    ``LITPORTAL_DUAL_WRITE_LITHUB`` is enabled, each record is ALSO written to
    the central LitHub library so the user sees the paper in LitPulse's
    ``/api/library`` listing as well. The LitHub write happens out-of-band
    (background task + outbox-with-retry) so the user-facing response shape
    and latency are unchanged regardless of LitHub availability.
    """
    try:
        items = await service.add_records(collection_id, user.id, payload)
    except LitBridgeError as exc:
        raise _map_collection_error(exc) from exc

    extraction_items = [
        {
            "item_id": item.id,
            "title": item.title or "",
            "record_id": item.record_id,
            "search_session_id": item.search_session_id,
        }
        for item in items
    ]
    if extraction_items:
        background_tasks.add_task(
            _run_extraction_background,
            extraction_items,
            request.app.state.db_session_factory,
            llm,
            request.app.state.redis,
        )

    settings = get_settings()
    if settings.LITPORTAL_DUAL_WRITE_LITHUB and items:
        # The central LitHub library is keyed by the user's Identity ``sub``
        # (NOT the litbridge-local user id) so a paper saved here matches what
        # LitPulse reads for the same user. ``identity_id`` is stamped on the
        # shadow row by get_current_user on the first Identity-authenticated
        # request, so it is present here. If it is missing (Identity disabled
        # or a pre-migration native user), skip the LitHub mirror.
        identity_sub = getattr(user, "identity_id", None)
        if identity_sub is not None:
            background_tasks.add_task(
                _run_lithub_sync_background,
                str(identity_sub),
                [
                    {
                        "item_id": str(item.id),
                        "record_id": item.record_id,
                        "title": item.title,
                        "search_session_id": str(item.search_session_id),
                    }
                    for item in items
                ],
                request.app.state.db_session_factory,
            )
        else:
            logger.warning(
                "lithub_sync_skipped_no_identity_id",
                user_id=str(user.id),
            )

    return items


async def _run_lithub_sync_background(
    identity_user_id: str,
    item_dicts: list[dict[str, Any]],
    session_factory: Any,
) -> None:
    """Fan out post-commit LitHub writes for the freshly-added records.

    For each item, we look up the SearchSession's serialized ``results`` to
    find the UnifiedRecord with the matching ``record_id``, extract its PMID
    and DOI, and call LitHub's save endpoint via :class:`LitHubSyncService`
    keyed by the user's Identity ``sub``. On success the returned LitHub
    ``paper_id`` is persisted onto the collection item so collection listing
    can enrich from LitHub. LitHub failures are absorbed by the outbox.
    """
    from src.clients.lithub_client import LitHubClient
    from src.repositories.lithub_sync_repo import LitHubSyncRepository
    from src.repositories.research_collection_repo import ResearchCollectionRepository
    from src.repositories.search_repo import SearchRepository as _SearchRepo
    from src.services.lithub_sync_service import LitHubSyncService as _SyncSvc

    settings = get_settings()
    if not settings.LITPORTAL_DUAL_WRITE_LITHUB or not settings.LITHUB_BASE_URL:
        return

    import httpx

    async with httpx.AsyncClient(timeout=httpx.Timeout(8.0)) as client:
        async with session_factory() as session:
            search_repo = _SearchRepo(db=session)
            outbox = LitHubSyncRepository(db=session)
            collection_repo = ResearchCollectionRepository(db=session)
            lithub = LitHubClient(http_client=client, settings=settings)
            sync = _SyncSvc(lithub=lithub, outbox=outbox)

            for item in item_dicts:
                # One bad record must never abort the whole batch — wrap each.
                try:
                    record_id = item["record_id"]
                    search_session_id = UUID(item["search_session_id"])
                    paper_record = await _extract_paper_identifiers(
                        search_repo, search_session_id, record_id,
                    )
                    if paper_record is None:
                        logger.warning(
                            "lithub_sync_record_not_found",
                            record_id=record_id,
                            search_session_id=str(search_session_id),
                        )
                        continue
                    save_body = {
                        **paper_record,
                        "source": "litportal-collection",
                        "folder": "Inbox",
                        "title": item.get("title") or paper_record.get("title") or "Untitled paper",
                    }
                    ok, response = await sync.save_paper(UUID(identity_user_id), save_body)
                    if ok and response and response.get("paper_id"):
                        await collection_repo.set_item_paper_id(
                            UUID(item["item_id"]), UUID(str(response["paper_id"])),
                        )
                except Exception as exc:  # noqa: BLE001 — per-item best-effort
                    logger.warning(
                        "lithub_sync_item_failed",
                        item=item.get("record_id"),
                        error=str(exc),
                    )


async def _extract_paper_identifiers(
    search_repo: SearchRepository,
    search_session_id: UUID,
    record_id: str,
) -> dict[str, Any] | None:
    """Pull the canonical paper identifiers out of a SearchSession result row."""
    # SearchRepository exposes get_session(<uuid-string>), not get_by_id.
    session_row = await search_repo.get_session(str(search_session_id))
    if session_row is None or not session_row.results:
        return None
    for record in session_row.results:
        if not isinstance(record, dict):
            continue
        if record.get("id") != record_id:
            continue
        result: dict[str, Any] = {}
        if record.get("pmid"):
            result["pmid"] = str(record["pmid"])
        if record.get("doi"):
            result["doi"] = str(record["doi"])
        if not result:
            return None
        for k in ("title", "abstract", "journal", "ai_summary"):
            if record.get(k):
                result[k] = record[k]
        if record.get("year"):
            result["year"] = record["year"]
        if record.get("pub_date"):
            result["pub_date"] = record["pub_date"]
        authors = record.get("authors")
        if isinstance(authors, list):
            result["authors"] = authors
        elif isinstance(authors, str) and authors:
            result["authors"] = [authors]
        study_design = record.get("study_design")
        if study_design:
            result["study_design"] = study_design
        if record.get("portal_engine_record_id"):
            result["portal_engine_record_id"] = record["portal_engine_record_id"]
        else:
            result["portal_engine_record_id"] = record_id
        return result
    return None


@router.delete("/{collection_id}/records/{record_id}", status_code=204)
async def remove_record(
    collection_id: UUID,
    record_id: str,
    user: User = Depends(get_current_user),
    service: ResearchCollectionService = Depends(get_research_collection_service),
) -> None:
    """Remove a record from a research collection."""

    try:
        await service.remove_record(collection_id, record_id, user.id)
    except LitBridgeError as exc:
        raise _map_collection_error(exc) from exc


@router.post(
    "/{collection_id}/records/{record_id}/move",
    response_model=CollectionItemResponse,
)
async def move_record(
    collection_id: UUID,
    record_id: str,
    payload: MoveRecordRequest,
    user: User = Depends(get_current_user),
    service: ResearchCollectionService = Depends(get_research_collection_service),
) -> CollectionItemResponse:
    """Move a record from this collection to another one."""

    try:
        return await service.move_record(
            collection_id, record_id, user.id, payload,
        )
    except LitBridgeError as exc:
        raise _map_collection_error(exc) from exc


@router.post(
    "/{collection_id}/records/{record_id}/extract",
    response_model=PaperMetadata,
)
async def extract_record_metadata(
    collection_id: UUID,
    record_id: str,
    user: User = Depends(get_current_user),
    service: ResearchCollectionService = Depends(get_research_collection_service),
    extraction: PaperExtractionService = Depends(get_paper_extraction_service),
) -> PaperMetadata:
    """Manually trigger AI metadata extraction (or re-extraction) for a record."""

    try:
        collection = await service.get_collection(collection_id, user.id)
    except LitBridgeError as exc:
        raise _map_collection_error(exc) from exc

    item = next(
        (it for it in collection.items if it.record_id == record_id),
        None,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Record not found in this collection")

    return await extraction.extract_and_persist(
        item_id=item.id,
        title=item.title or "",
        abstract=None,
        record_id=record_id,
        search_session_id=item.search_session_id,
    )
