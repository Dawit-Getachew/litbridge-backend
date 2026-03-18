"""Workflow orchestrator -- manages the multi-step structured search workflow."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog
from redis.asyncio import Redis

from src.ai.llm_client import LLMClient
from src.core.config import Settings
from src.core.exceptions import SearchNotFoundError
from src.schemas.enums import SearchMode, SourceType
from src.services.dedup_service import DedupService
from src.services.fetcher_service import FetcherService
from src.repositories import get_repository
from src.repositories.search_repo import SearchRepository
from src.schemas.records import RawRecord
from src.schemas.search import SearchRequest
from src.services.pico_fill_service import fill_missing_pico_state
from src.workflow.agents.keyword_agent import run_keyword_expansion
from src.workflow.agents.mesh_agent import run_mesh_resolution
from src.workflow.agents.pico_agent import run_pico_extraction
from src.workflow.agents.pico_recommender import run_pico_recommendation
from src.workflow.mesh_resolver import (
    descriptor_to_mesh_suggestion,
    resolve_mesh_descriptor,
)
from src.workflow.query_adapter import adapt_all
from src.workflow.query_builder import build_structured_query
from src.workflow.state import (
    MeshDescriptor,
    PicoElement,
    StructuredQuery,
    Suggestion,
    WorkflowState,
)
from src.workflow.state_store import load_state, save_state

logger = structlog.get_logger(__name__)


def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


class WorkflowService:
    """Orchestrate the structured search workflow with HITL checkpoints."""

    def __init__(
        self,
        llm: LLMClient,
        http_client: httpx.AsyncClient,
        redis: Redis,
        settings: Settings,
        fetcher: FetcherService,
        dedup: DedupService,
        search_repo: SearchRepository,
    ) -> None:
        self.llm = llm
        self.http_client = http_client
        self.redis = redis
        self.settings = settings
        self.fetcher = fetcher
        self.dedup = dedup
        self.search_repo = search_repo

    async def _load_or_404(self, session_id: str) -> WorkflowState:
        state = await load_state(self.redis, session_id)
        if state is None:
            raise SearchNotFoundError(session_id, "Workflow session not found or expired.")
        return state

    async def start_workflow(
        self,
        question: str,
        query_type: str = "free",
        pico_input: dict[str, str] | None = None,
        user_id: str | None = None,
    ) -> WorkflowState:
        """Start a new workflow: extract PICO, expand keywords, return for review."""
        session_id = str(uuid.uuid4())

        if pico_input and query_type == "structured":
            lines: list[str] = []
            for key in ("P", "I", "C", "O"):
                val = pico_input.get(key) or pico_input.get(key.lower())
                if val and str(val).strip():
                    lines.append(f"{key}: {str(val).strip()}")
            question_text = "\n".join(lines) if lines else question
        else:
            question_text = question

        state = WorkflowState(
            session_id=session_id,
            user_id=user_id,
            question=question_text,
            original_query_type=query_type,
        )

        state = await run_pico_extraction(state, self.llm)
        state = await fill_missing_pico_state(state, self.llm)
        state = await run_pico_recommendation(
            state,
            client=self.http_client,
            api_key=self.settings.NCBI_API_KEY,
            email=self.settings.CONTACT_EMAIL,
        )
        state = await run_keyword_expansion(state, self.llm)

        await save_state(self.redis, state)
        return state

    async def pico_preview(self, question: str) -> dict[str, list[dict]]:
        """Quick stateless PICO extraction preview."""
        state = WorkflowState(
            session_id="preview",
            question=question,
        )
        state = await run_pico_extraction(state, self.llm)
        state = await fill_missing_pico_state(state, self.llm)
        return {
            concept: [el.model_dump() for el in elements]
            for concept, elements in state.pico.items()
        }

    async def process_keyword_feedback(
        self,
        session_id: str,
        pico_edits: list[dict[str, Any]],
        keyword_decisions: list[dict[str, Any]],
    ) -> WorkflowState:
        """Apply PICO edits + keyword feedback, then run MeSH resolution."""
        state = await self._load_or_404(session_id)

        for edit in pico_edits:
            concept = str(edit.get("concept", "")).upper()
            action = str(edit.get("action", ""))
            if concept not in ("P", "I", "C", "O"):
                continue

            if action == "add":
                text = str(edit.get("text", "")).strip()
                if text:
                    state.pico[concept].append(
                        PicoElement(text=text, provenance="user")
                    )
                    if text not in state.atomic_targets.get(concept, []):
                        state.atomic_targets.setdefault(concept, []).append(text)

            elif action == "remove":
                index = edit.get("index")
                if isinstance(index, int) and 0 <= index < len(state.pico[concept]):
                    removed = state.pico[concept].pop(index)
                    if removed.text in state.atomic_targets.get(concept, []):
                        state.atomic_targets[concept].remove(removed.text)

            elif action == "edit":
                index = edit.get("index")
                text = str(edit.get("text", "")).strip()
                if isinstance(index, int) and 0 <= index < len(state.pico[concept]) and text:
                    old_text = state.pico[concept][index].text
                    state.pico[concept][index].text = text
                    state.pico[concept][index].provenance = "user"
                    targets = state.atomic_targets.get(concept, [])
                    if old_text in targets:
                        idx = targets.index(old_text)
                        targets[idx] = text
                    elif text not in targets:
                        targets.append(text)

        for item in keyword_decisions:
            concept = str(item.get("concept", "")).upper()
            base_term = str(item.get("base_term", "")).strip()
            decisions = item.get("decisions", [])
            if concept not in ("P", "I", "C", "O") or not base_term:
                continue

            bucket = state.synonyms.get(concept, {}).get(base_term, [])

            for dec in decisions:
                action = str(dec.get("action", ""))
                term = str(dec.get("term", "")).strip()
                new_term = str(dec.get("new_term", "")).strip()

                if action == "accept" and term:
                    for s in bucket:
                        if _norm(s.term) == _norm(term):
                            s.status = "accepted"

                elif action == "reject" and term:
                    bucket = [s for s in bucket if _norm(s.term) != _norm(term)]

                elif action == "edit" and term and new_term:
                    for s in bucket:
                        if _norm(s.term) == _norm(term):
                            s.term = new_term
                            s.status = "accepted"

                elif action == "add" and new_term:
                    if not any(_norm(s.term) == _norm(new_term) for s in bucket):
                        bucket.append(
                            Suggestion(
                                term=new_term,
                                concept=concept,
                                base_term=base_term,
                                status="accepted",
                                variant="synonym",
                            )
                        )

            state.synonyms.setdefault(concept, {})[base_term] = bucket

        state = await run_mesh_resolution(
            state,
            client=self.http_client,
            api_key=self.settings.NCBI_API_KEY,
            email=self.settings.CONTACT_EMAIL,
        )

        await save_state(self.redis, state)
        return state

    async def resolve_single_mesh(
        self,
        session_id: str,
        concept: str,
        base_term: str,
        mesh_term: str,
    ) -> tuple[WorkflowState, MeshDescriptor | None]:
        """Manually resolve a MeSH term the user typed."""
        state = await self._load_or_404(session_id)

        descriptor, _translation = await resolve_mesh_descriptor(
            term=mesh_term,
            client=self.http_client,
            api_key=self.settings.NCBI_API_KEY,
            email=self.settings.CONTACT_EMAIL,
        )

        suggestion: MeshDescriptor | None = None

        if descriptor:
            suggestion = descriptor_to_mesh_suggestion(
                descriptor,
                concept=concept,
                base_term=base_term,
                status="suggested",
            )
        else:
            suggestion = MeshDescriptor(
                mesh_term=mesh_term,
                concept=concept,
                base_term=base_term,
                status="suggested",
                scope_note="No canonical MeSH descriptor found.",
            )

        state.mesh.setdefault(concept, {}).setdefault(base_term, [])
        norm_new = _norm(mesh_term)
        state.mesh[concept][base_term] = [
            m for m in state.mesh[concept][base_term]
            if _norm(m.mesh_term) != norm_new
        ]
        state.mesh[concept][base_term].append(suggestion)

        await save_state(self.redis, state)
        return state, suggestion

    async def process_mesh_feedback(
        self,
        session_id: str,
        items: list[dict[str, Any]],
    ) -> WorkflowState:
        """Apply MeSH feedback, build Boolean, return query for review."""
        state = await self._load_or_404(session_id)

        for item in items:
            concept = str(item.get("concept", "")).upper()
            base_term = str(item.get("base_term", "")).strip()
            mesh_term = str(item.get("mesh_term", "")).strip()
            action = str(item.get("action", "none"))
            entry_selected = item.get("entry_terms_selected", [])
            quals_selected = item.get("qualifiers_selected", [])
            explode = item.get("explode", True)

            if concept not in ("P", "I", "C", "O") or not base_term:
                continue

            bucket = state.mesh.get(concept, {}).get(base_term, [])

            if action == "reject":
                norm_key = _norm(mesh_term)
                bucket = [m for m in bucket if _norm(m.mesh_term) != norm_key]
                state.mesh.setdefault(concept, {})[base_term] = bucket
                continue

            found: MeshDescriptor | None = None
            for m in bucket:
                if _norm(m.mesh_term) == _norm(mesh_term):
                    found = m
                    break

            if found is None:
                continue

            if action == "accept":
                found.status = "accepted"
            found.explode = bool(explode)

            if isinstance(entry_selected, list):
                found.entry_terms_selected = [
                    str(e).strip() for e in entry_selected if str(e).strip()
                ]

            if isinstance(quals_selected, list) and found.qualifiers:
                allowed_lower = {a.lower() for a in found.qualifiers.allowed}
                if allowed_lower:
                    found.qualifiers.selected = [
                        q for q in quals_selected if q.lower() in allowed_lower
                    ]

        query = build_structured_query(state)
        query.adapted_queries = adapt_all(query)
        state.structured_query = query
        state.awaiting = "query_review"

        await save_state(self.redis, state)
        return state

    async def update_query(
        self,
        session_id: str,
        edited_pubmed_query: str | None = None,
    ) -> WorkflowState:
        """Allow the user to manually edit the Boolean query."""
        state = await self._load_or_404(session_id)

        if state.structured_query is None:
            state.structured_query = StructuredQuery()

        if edited_pubmed_query is not None:
            state.structured_query.pubmed_query = edited_pubmed_query.strip()

        await save_state(self.redis, state)
        return state

    async def execute_search(
        self,
        session_id: str,
        search_mode: str = "quick",
        sources: list[str] | None = None,
        max_results: int = 100,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute the approved search through the existing pipeline.

        Returns a dict with search_id that works with all existing endpoints.
        """
        state = await self._load_or_404(session_id)

        if state.structured_query is None:
            query = build_structured_query(state)
            query.adapted_queries = adapt_all(query)
            state.structured_query = query

        selected_sources = sources or [s.value for s in SourceType]
        valid_values = {st.value for st in SourceType}
        source_enums = [SourceType(s) for s in selected_sources if s in valid_values]

        if not source_enums:
            raise SearchNotFoundError(
                session_id,
                "No valid sources specified. Choose from: "
                + ", ".join(sorted(valid_values)),
            )

        mode = SearchMode(search_mode) if search_mode in {m.value for m in SearchMode} else SearchMode.QUICK

        search_request = SearchRequest(
            query=state.structured_query.pubmed_query or state.question,
            query_type="boolean",
            search_mode=mode,
            sources=source_enums,
            max_results=max_results,
            workflow=True,
        )
        from uuid import UUID as _UUID
        uid = _UUID(user_id) if user_id else (
            _UUID(state.user_id) if state.user_id else None
        )
        session = await self.search_repo.create_session(search_request, user_id=uid)

        query_map: dict[SourceType, str] = {}
        adapted = state.structured_query.adapted_queries
        for src in source_enums:
            query_map[src] = adapted.get(src.value, state.structured_query.pubmed_query)

        effective_max = FetcherService._max_results_for_mode(mode, max_results)

        all_raw: list[RawRecord] = []
        sources_completed: list[str] = []
        sources_failed: list[str] = []

        async def fetch_one(source: SourceType) -> tuple[SourceType, list[RawRecord], str | None]:
            repo = get_repository(source=source, client=self.http_client)
            q = query_map.get(source, state.structured_query.pubmed_query)
            try:
                records = await repo.search(query=q, max_results=effective_max)
                return source, records, None
            except Exception as exc:
                return source, [], str(exc)

        tasks = [asyncio.create_task(fetch_one(s)) for s in source_enums]
        results = await asyncio.gather(*tasks)

        for source, records, error in results:
            if error:
                sources_failed.append(source.value)
                logger.warning("workflow_source_failed", source=source.value, error=error)
            else:
                all_raw.extend(records)
                sources_completed.append(source.value)

        unified = self.dedup.deduplicate(all_raw)
        search_id = str(session.id)

        session.status = "completed"
        session.total_identified = len(all_raw)
        session.total_after_dedup = len(unified)
        session.sources_completed = sources_completed
        session.sources_failed = sources_failed
        session.pico = {
            c: [el.model_dump() for el in elements]
            for c, elements in state.pico.items()
        }
        session.completed_at = datetime.now(timezone.utc)
        await self.search_repo.update_session(session)
        await self.search_repo.store_results(search_id, unified)

        state.search_id = search_id
        state.awaiting = None
        await save_state(self.redis, state)

        return {
            "search_id": search_id,
            "workflow_session_id": session_id,
            "total_identified": len(all_raw),
            "total_after_dedup": len(unified),
            "sources_completed": sources_completed,
            "sources_failed": sources_failed,
        }
