"""Conversational chat service for follow-up questions over search results."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator

import structlog

from src.ai.llm_client import CHAT_SYSTEM_PROMPT, LLMClient
from src.repositories.conversation_repo import ConversationRepository
from src.schemas.chat import ChatRequest, ResolvedRecord
from src.schemas.records import UnifiedRecord
from src.schemas.streaming import StreamEvent
from src.services.record_resolver import resolve_references

DEFAULT_MAX_HISTORY_TURNS = 10
DEFAULT_MAX_CONTEXT_RECORDS = 25


class ChatService:
    """Orchestrate conversational AI over search results with streaming."""

    def __init__(
        self,
        conversation_repo: ConversationRepository,
        llm_client: LLMClient,
        max_history_turns: int = DEFAULT_MAX_HISTORY_TURNS,
        max_context_records: int = DEFAULT_MAX_CONTEXT_RECORDS,
    ) -> None:
        self.conversation_repo = conversation_repo
        self.llm_client = llm_client
        self.max_history_turns = max_history_turns
        self.max_context_records = max_context_records
        self.logger = structlog.get_logger(__name__).bind(service="chat_service")

    async def stream_chat(
        self,
        request: ChatRequest,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Handle a chat message: resolve records, build context, stream response."""

        # 1. Load search results for context
        all_records = await self.conversation_repo.get_search_records(request.search_id)
        if not all_records:
            yield StreamEvent(
                event="error",
                data={"error": f"Search session {request.search_id} not found or has no results."},
            )
            return

        # 2. Get or create conversation
        conversation_id = request.conversation_id
        if conversation_id:
            conversation = await self.conversation_repo.get_conversation(conversation_id)
            if conversation is None:
                yield StreamEvent(
                    event="error",
                    data={"error": f"Conversation {conversation_id} not found."},
                )
                return
        else:
            title = request.message[:80] if len(request.message) > 80 else request.message
            conversation = await self.conversation_repo.create_conversation(
                search_session_id=request.search_id,
                title=title,
            )
            conversation_id = str(conversation.id)

        # 3. Resolve grounding records.
        #    Week-1 LitPortal merger (proposal §3.3.B): when the client sends
        #    an explicit `selected_record_ids` set, those are the ground-truth
        #    context — the LLM must cite only from them. Unknown ids are
        #    silently dropped with a warning so a stale frontend cache cannot
        #    hard-fail the answer; if every supplied id is unknown we fall
        #    back to natural-language resolution to keep the request usable.
        if request.selected_record_ids:
            selected_set = {rid for rid in request.selected_record_ids if rid}
            resolved = [r for r in all_records if r.id in selected_set]
            unknown = selected_set - {r.id for r in resolved}
            if unknown:
                self.logger.warning(
                    "chat_selected_ids_unknown",
                    search_id=request.search_id,
                    unknown_count=len(unknown),
                    accepted_count=len(resolved),
                )
            if not resolved:
                # Every id was stale — fall back rather than send an empty
                # context to the LLM (which would produce uncited speculation).
                resolved = resolve_references(request.message, all_records)
        else:
            resolved = resolve_references(request.message, all_records)

        resolved_summaries = [
            ResolvedRecord(
                id=r.id,
                title=r.title,
                first_author=r.authors[0] if r.authors else None,
                year=r.year,
            )
            for r in resolved
        ]
        resolved_ids = [r.id for r in resolved]

        yield StreamEvent(
            event="chat_started",
            data={
                "conversation_id": conversation_id,
                "resolved_records": [rec.model_dump(mode="json") for rec in resolved_summaries],
            },
        )

        # 4. Persist the user message
        await self.conversation_repo.add_message(
            conversation_id=conversation_id,
            role="user",
            content=request.message,
            record_ids=resolved_ids or None,
        )

        # 5. Build LLM message list
        messages = self._build_messages(
            user_message=request.message,
            all_records=all_records,
            resolved_records=resolved,
            conversation=conversation,
            grounding_strict=bool(request.selected_record_ids and resolved),
        )

        # 6. Stream response token-by-token
        full_response: list[str] = []
        async for chunk in self.llm_client.stream_chat(messages):
            full_response.append(chunk)
            yield StreamEvent(event="thinking", data={"chunk": chunk})

        # 7. Persist the assistant response
        assistant_content = "".join(full_response)
        if assistant_content.strip():
            message = await self.conversation_repo.add_message(
                conversation_id=conversation_id,
                role="assistant",
                content=assistant_content,
                record_ids=resolved_ids or None,
            )
            yield StreamEvent(
                event="chat_completed",
                data={
                    "conversation_id": conversation_id,
                    "message_id": str(message.id),
                },
            )
        else:
            # The LLM stream yielded zero content. Most common causes are an
            # upstream HTTP error (auth, rate limit, quota) — surfaced as a
            # warning log in src/ai/llm_client.py — or a configuration issue.
            # Emit an explicit ``error`` event so the frontend can render a
            # meaningful message instead of an empty answer, then close with
            # ``chat_completed`` so existing clients still terminate cleanly.
            self.logger.warning(
                "chat_llm_empty_response",
                search_id=request.search_id,
                conversation_id=conversation_id,
                resolved_record_count=len(resolved_ids),
            )
            yield StreamEvent(
                event="error",
                data={
                    "error": (
                        "The language model returned no content. The LLM "
                        "provider may be unavailable, rate-limited, or out "
                        "of quota. Check Portal Engine logs for the upstream "
                        "status code (look for 'llm_stream_http_error')."
                    ),
                    "code": "llm_empty_response",
                },
            )
            yield StreamEvent(
                event="chat_completed",
                data={"conversation_id": conversation_id, "message_id": None},
            )

    def _build_messages(
        self,
        user_message: str,
        all_records: list[UnifiedRecord],
        resolved_records: list[UnifiedRecord],
        conversation,
        grounding_strict: bool = False,
    ) -> list[dict[str, str]]:
        """Construct the full message list for the LLM call.

        When ``grounding_strict`` is true, the resolved_records came from an
        explicit ``selected_record_ids`` list (Week-1 LitPortal contract). The
        prompt is tightened so the model cites only those records and uses
        ``[n]`` markers matching the numbered list — the LitPortal grounded-
        answer panel renders those markers as inline footnotes.
        """

        # System prompt with search context
        context_records = resolved_records if resolved_records else all_records[:self.max_context_records]
        records_context = self._format_records_context(context_records)

        search_session = None
        if hasattr(conversation, "search_session_id"):
            search_session = conversation.search_session_id

        system_content = (
            f"{CHAT_SYSTEM_PROMPT}\n\n"
            f"## Search Results Context\n"
            f"The user's search returned {len(all_records)} articles. "
        )

        if grounding_strict:
            system_content += (
                f"The user has explicitly selected the following {len(resolved_records)} "
                f"paper(s) to ground your answer. You must cite only these papers and "
                f"reference them using bracketed numeric markers that match the list "
                f"below (e.g. [1], [2]). Do not introduce other papers, do not invent "
                f"citations, and explicitly note any limitations the selected evidence "
                f"cannot resolve.\n\n"
                f"{records_context}"
            )
        elif resolved_records:
            system_content += (
                f"The user appears to be referring to the following {len(resolved_records)} paper(s):\n\n"
                f"{records_context}"
            )
        else:
            system_content += (
                f"Here are the top {len(context_records)} results for reference:\n\n"
                f"{records_context}"
            )

        messages: list[dict[str, str]] = [{"role": "system", "content": system_content}]

        # Conversation history (truncated to recent turns)
        if hasattr(conversation, "messages") and conversation.messages:
            history = conversation.messages
            max_messages = self.max_history_turns * 2
            if len(history) > max_messages:
                history = history[-max_messages:]

            for msg in history:
                messages.append({"role": msg.role, "content": msg.content})

        # Current user message
        messages.append({"role": "user", "content": user_message})

        return messages

    def _format_records_context(self, records: list[UnifiedRecord]) -> str:
        """Format records into a readable context block for the LLM."""
        entries = []
        for i, record in enumerate(records, 1):
            authors_str = ", ".join(record.authors[:3])
            if len(record.authors) > 3:
                authors_str += " et al."

            entry = f"[{i}] {record.title}\n"
            entry += f"    Authors: {authors_str}\n"
            if record.year:
                entry += f"    Year: {record.year}\n"
            if record.journal:
                entry += f"    Journal: {record.journal}\n"
            if record.doi:
                entry += f"    DOI: {record.doi}\n"
            if record.citation_count is not None:
                entry += f"    Citations: {record.citation_count}\n"
            if record.tldr:
                entry += f"    TLDR: {record.tldr}\n"
            if record.abstract:
                abstract_preview = record.abstract[:500]
                if len(record.abstract) > 500:
                    abstract_preview += "..."
                entry += f"    Abstract: {abstract_preview}\n"

            entries.append(entry)

        return "\n".join(entries)
