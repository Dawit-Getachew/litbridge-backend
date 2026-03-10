"""Workflow API routes for structured search with human-in-the-loop."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from redis.asyncio import Redis

from src.ai.llm_client import LLMClient
from src.core.config import Settings
from src.core.deps import (
    get_current_user_optional,
    get_dedup_service,
    get_fetcher_service,
    get_http_client,
    get_llm_client,
    get_redis,
    get_search_repo,
    get_settings,
)
from src.core.exceptions import SearchNotFoundError
from src.models.user import User
from src.repositories.search_repo import SearchRepository
from src.schemas.workflow import (
    KeywordFeedbackRequest,
    KeywordFeedbackResponse,
    MeshFeedbackRequest,
    MeshFeedbackResponse,
    MeshResolveRequest,
    MeshResolveResponse,
    PicoPreviewRequest,
    PicoPreviewResponse,
    QueryEditRequest,
    QueryPreviewResponse,
    WorkflowSearchRequest,
    WorkflowSearchResponse,
    WorkflowStartRequest,
    WorkflowStartResponse,
)
from src.services.dedup_service import DedupService
from src.services.fetcher_service import FetcherService
from src.services.workflow_service import WorkflowService
from src.workflow.query_adapter import adapt_all
from src.workflow.query_builder import build_structured_query
from src.workflow.state_store import load_state, save_state

router = APIRouter(prefix="/workflow", tags=["Workflow"])


def _build_service(
    llm: LLMClient,
    http_client: httpx.AsyncClient,
    redis: Redis,
    settings: Settings,
    fetcher: FetcherService,
    dedup: DedupService,
    search_repo: SearchRepository,
) -> WorkflowService:
    return WorkflowService(
        llm=llm,
        http_client=http_client,
        redis=redis,
        settings=settings,
        fetcher=fetcher,
        dedup=dedup,
        search_repo=search_repo,
    )


def _state_to_pico_dict(state) -> dict[str, list[dict[str, Any]]]:
    return {
        c: [el.model_dump() for el in elements]
        for c, elements in state.pico.items()
    }


def _state_to_keywords_dict(state) -> dict[str, dict[str, list[dict[str, Any]]]]:
    return {
        c: {
            bt: [s.model_dump() for s in suggestions]
            for bt, suggestions in groups.items()
        }
        for c, groups in state.synonyms.items()
    }


def _state_to_mesh_dict(state) -> dict[str, dict[str, list[dict[str, Any]]]]:
    return {
        c: {
            bt: [m.model_dump() for m in descriptors]
            for bt, descriptors in groups.items()
        }
        for c, groups in state.mesh.items()
    }


@router.post("/start", response_model=WorkflowStartResponse, status_code=201)
async def start_workflow(
    payload: WorkflowStartRequest,
    llm=Depends(get_llm_client),
    http_client=Depends(get_http_client),
    redis=Depends(get_redis),
    settings=Depends(get_settings),
    fetcher=Depends(get_fetcher_service),
    dedup=Depends(get_dedup_service),
    search_repo=Depends(get_search_repo),
    user: User | None = Depends(get_current_user_optional),
) -> WorkflowStartResponse:
    """Start a new structured search workflow session."""
    if not payload.query.strip() and not payload.pico:
        raise HTTPException(status_code=422, detail="Provide either query or pico.")

    svc = _build_service(llm, http_client, redis, settings, fetcher, dedup, search_repo)

    question = payload.query.strip()
    if not question and payload.pico:
        parts = []
        for k in ("P", "I", "C", "O"):
            v = payload.pico.get(k) or payload.pico.get(k.lower())
            if v and str(v).strip():
                parts.append(f"{k}: {str(v).strip()}")
        question = "\n".join(parts)

    state = await svc.start_workflow(
        question=question,
        query_type=payload.query_type,
        pico_input=payload.pico,
        user_id=str(user.id) if user else None,
    )

    return WorkflowStartResponse(
        workflow_session_id=state.session_id,
        awaiting=state.awaiting,
        pico=_state_to_pico_dict(state),
        keywords=_state_to_keywords_dict(state),
        errors=state.errors,
    )


@router.post("/pico-preview", response_model=PicoPreviewResponse)
async def pico_preview(
    payload: PicoPreviewRequest,
    llm=Depends(get_llm_client),
    http_client=Depends(get_http_client),
    redis=Depends(get_redis),
    settings=Depends(get_settings),
    fetcher=Depends(get_fetcher_service),
    dedup=Depends(get_dedup_service),
    search_repo=Depends(get_search_repo),
    user: User | None = Depends(get_current_user_optional),
) -> PicoPreviewResponse:
    """Quick PICO extraction preview without creating a session."""
    svc = _build_service(llm, http_client, redis, settings, fetcher, dedup, search_repo)
    pico = await svc.pico_preview(payload.question)
    return PicoPreviewResponse(pico=pico)


@router.post(
    "/{session_id}/keywords/feedback",
    response_model=KeywordFeedbackResponse,
)
async def submit_keyword_feedback(
    session_id: str,
    payload: KeywordFeedbackRequest,
    llm=Depends(get_llm_client),
    http_client=Depends(get_http_client),
    redis=Depends(get_redis),
    settings=Depends(get_settings),
    fetcher=Depends(get_fetcher_service),
    dedup=Depends(get_dedup_service),
    search_repo=Depends(get_search_repo),
    user: User | None = Depends(get_current_user_optional),
) -> KeywordFeedbackResponse:
    """Accept PICO edits + keyword feedback, then resolve MeSH."""
    svc = _build_service(llm, http_client, redis, settings, fetcher, dedup, search_repo)

    state = await svc.process_keyword_feedback(
        session_id=session_id,
        pico_edits=[e.model_dump() for e in payload.pico_edits],
        keyword_decisions=[
            {
                "concept": item.concept,
                "base_term": item.base_term,
                "decisions": [d.model_dump() for d in item.decisions],
            }
            for item in payload.keyword_decisions
        ],
    )

    return KeywordFeedbackResponse(
        workflow_session_id=state.session_id,
        awaiting=state.awaiting,
        mesh=_state_to_mesh_dict(state),
        errors=state.errors,
    )


@router.post(
    "/{session_id}/mesh/resolve",
    response_model=MeshResolveResponse,
)
async def resolve_mesh_term(
    session_id: str,
    payload: MeshResolveRequest,
    llm=Depends(get_llm_client),
    http_client=Depends(get_http_client),
    redis=Depends(get_redis),
    settings=Depends(get_settings),
    fetcher=Depends(get_fetcher_service),
    dedup=Depends(get_dedup_service),
    search_repo=Depends(get_search_repo),
    user: User | None = Depends(get_current_user_optional),
) -> MeshResolveResponse:
    """Manually resolve a MeSH term the user typed."""
    svc = _build_service(llm, http_client, redis, settings, fetcher, dedup, search_repo)

    _state, suggestion = await svc.resolve_single_mesh(
        session_id=session_id,
        concept=payload.concept.upper(),
        base_term=payload.base_term,
        mesh_term=payload.mesh_term,
    )

    found = suggestion is not None and suggestion.descriptor_uid is not None
    return MeshResolveResponse(
        found=found,
        suggestion=suggestion.model_dump() if suggestion else None,
        message=None if found else "No canonical MeSH descriptor found.",
    )


@router.post(
    "/{session_id}/mesh/feedback",
    response_model=MeshFeedbackResponse,
)
async def submit_mesh_feedback(
    session_id: str,
    payload: MeshFeedbackRequest,
    llm=Depends(get_llm_client),
    http_client=Depends(get_http_client),
    redis=Depends(get_redis),
    settings=Depends(get_settings),
    fetcher=Depends(get_fetcher_service),
    dedup=Depends(get_dedup_service),
    search_repo=Depends(get_search_repo),
    user: User | None = Depends(get_current_user_optional),
) -> MeshFeedbackResponse:
    """Accept MeSH feedback, build Boolean query."""
    svc = _build_service(llm, http_client, redis, settings, fetcher, dedup, search_repo)

    state = await svc.process_mesh_feedback(
        session_id=session_id,
        items=[item.model_dump() for item in payload.items],
    )

    query_data = None
    if state.structured_query:
        query_data = state.structured_query.model_dump()

    return MeshFeedbackResponse(
        workflow_session_id=state.session_id,
        awaiting=state.awaiting,
        query=query_data,
        errors=state.errors,
    )


@router.get("/{session_id}/query", response_model=QueryPreviewResponse)
async def preview_query(
    session_id: str,
    redis=Depends(get_redis),
    user: User | None = Depends(get_current_user_optional),
) -> QueryPreviewResponse:
    """Preview the built Boolean query and per-database adaptations."""
    state = await load_state(redis, session_id)
    if state is None:
        raise SearchNotFoundError(session_id, "Workflow session not found or expired.")

    if state.structured_query is None:
        query = build_structured_query(state)
        query.adapted_queries = adapt_all(query)
        state.structured_query = query
        await save_state(redis, state)

    sq = state.structured_query
    return QueryPreviewResponse(
        pubmed_query=sq.pubmed_query,
        adapted_queries=sq.adapted_queries,
        warnings=sq.warnings,
    )


@router.put("/{session_id}/query", response_model=QueryPreviewResponse)
async def edit_query(
    session_id: str,
    payload: QueryEditRequest,
    llm=Depends(get_llm_client),
    http_client=Depends(get_http_client),
    redis=Depends(get_redis),
    settings=Depends(get_settings),
    fetcher=Depends(get_fetcher_service),
    dedup=Depends(get_dedup_service),
    search_repo=Depends(get_search_repo),
    user: User | None = Depends(get_current_user_optional),
) -> QueryPreviewResponse:
    """Manually edit the Boolean query."""
    svc = _build_service(llm, http_client, redis, settings, fetcher, dedup, search_repo)

    state = await svc.update_query(
        session_id=session_id,
        edited_pubmed_query=payload.pubmed_query,
    )

    sq = state.structured_query
    return QueryPreviewResponse(
        pubmed_query=sq.pubmed_query if sq else "",
        adapted_queries=sq.adapted_queries if sq else {},
        warnings=sq.warnings if sq else [],
    )


@router.post(
    "/{session_id}/search",
    response_model=WorkflowSearchResponse,
)
async def execute_workflow_search(
    session_id: str,
    payload: WorkflowSearchRequest,
    llm=Depends(get_llm_client),
    http_client=Depends(get_http_client),
    redis=Depends(get_redis),
    settings=Depends(get_settings),
    fetcher=Depends(get_fetcher_service),
    dedup=Depends(get_dedup_service),
    search_repo=Depends(get_search_repo),
    user: User | None = Depends(get_current_user_optional),
) -> WorkflowSearchResponse:
    """Execute the approved structured search through the existing pipeline."""
    svc = _build_service(llm, http_client, redis, settings, fetcher, dedup, search_repo)

    result = await svc.execute_search(
        session_id=session_id,
        search_mode=payload.search_mode,
        sources=payload.sources,
        max_results=payload.max_results,
        user_id=str(user.id) if user else None,
    )

    return WorkflowSearchResponse(**result)
