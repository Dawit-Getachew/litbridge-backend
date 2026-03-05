"""Tests for the conversational chat service."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.schemas.chat import ChatRequest
from src.schemas.enums import OAStatus, SourceType
from src.schemas.records import UnifiedRecord
from src.schemas.streaming import StreamEvent
from src.services.chat_service import ChatService


def _build_record(
    record_id: str,
    title: str = "Test Paper",
    authors: list[str] | None = None,
    year: int | None = 2024,
) -> UnifiedRecord:
    return UnifiedRecord(
        id=record_id,
        title=title,
        authors=authors or ["Author One"],
        source=SourceType.PUBMED,
        year=year,
        abstract="This is a test abstract about important findings.",
        doi=f"10.1000/{record_id}",
    )


@dataclass
class FakeMessage:
    id: object = None
    role: str = ""
    content: str = ""
    record_ids: list[str] | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self):
        if self.id is None:
            self.id = uuid4()


@dataclass
class FakeConversation:
    id: object = None
    search_session_id: object = None
    title: str | None = None
    message_count: int = 0
    messages: list[FakeMessage] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self):
        if self.id is None:
            self.id = uuid4()
        if self.search_session_id is None:
            self.search_session_id = uuid4()


class FakeConversationRepo:
    """In-memory conversation repository for testing."""

    def __init__(self, records: list[UnifiedRecord] | None = None) -> None:
        self.records = records or []
        self.conversations: dict[str, FakeConversation] = {}
        self.messages: list[FakeMessage] = []

    async def get_search_records(self, search_id: str) -> list[UnifiedRecord]:
        return self.records

    async def create_conversation(self, search_session_id: str, title: str | None = None) -> FakeConversation:
        conv = FakeConversation(title=title)
        self.conversations[str(conv.id)] = conv
        return conv

    async def get_conversation(self, conversation_id: str) -> FakeConversation | None:
        return self.conversations.get(conversation_id)

    async def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        record_ids: list[str] | None = None,
    ) -> FakeMessage:
        msg = FakeMessage(role=role, content=content, record_ids=record_ids)
        self.messages.append(msg)
        return msg


class FakeLLMClient:
    """LLM client stub that yields predetermined chunks."""

    def __init__(self, chunks: list[str] | None = None) -> None:
        self.chunks = chunks or ["Hello ", "from ", "the AI."]
        self.last_messages: list[dict[str, str]] = []

    async def stream_chat(self, messages: list[dict[str, str]]) -> AsyncGenerator[str, None]:
        self.last_messages = messages
        for chunk in self.chunks:
            yield chunk


@pytest.mark.asyncio
async def test_stream_chat_creates_conversation_and_streams_response() -> None:
    """A new chat message should create a conversation and stream tokens."""
    records = [
        _build_record("r1", "Remdesivir for COVID-19", ["Zhang L"], 2024),
        _build_record("r2", "Dexamethasone Trial", ["Horby P"], 2020),
    ]
    repo = FakeConversationRepo(records=records)
    llm = FakeLLMClient(chunks=["Here ", "is the ", "answer."])

    service = ChatService(
        conversation_repo=repo,
        llm_client=llm,
        max_history_turns=10,
        max_context_records=25,
    )

    request = ChatRequest(search_id="some-search-id", message="Explain the Zhang 2024 paper")
    events = [event async for event in service.stream_chat(request)]

    event_types = [e.event for e in events]
    assert "chat_started" in event_types
    assert "thinking" in event_types
    assert "chat_completed" in event_types

    thinking_events = [e for e in events if e.event == "thinking"]
    full_text = "".join(e.data["chunk"] for e in thinking_events)
    assert full_text == "Here is the answer."


@pytest.mark.asyncio
async def test_stream_chat_resolves_records_and_includes_in_started_event() -> None:
    """Chat should resolve paper references and report them in chat_started."""
    records = [
        _build_record("r1", "Remdesivir for Treatment of COVID-19", ["Zhang L"], 2024),
    ]
    repo = FakeConversationRepo(records=records)
    llm = FakeLLMClient()

    service = ChatService(conversation_repo=repo, llm_client=llm)

    request = ChatRequest(search_id="s1", message="Explain the Zhang 2024 paper")
    events = [event async for event in service.stream_chat(request)]

    started = next(e for e in events if e.event == "chat_started")
    resolved = started.data.get("resolved_records", [])
    assert len(resolved) >= 1
    assert resolved[0]["id"] == "r1"


@pytest.mark.asyncio
async def test_stream_chat_persists_user_and_assistant_messages() -> None:
    """Both user message and assistant response should be persisted."""
    records = [_build_record("r1")]
    repo = FakeConversationRepo(records=records)
    llm = FakeLLMClient(chunks=["Test response."])

    service = ChatService(conversation_repo=repo, llm_client=llm)

    request = ChatRequest(search_id="s1", message="Tell me about this")
    _ = [event async for event in service.stream_chat(request)]

    assert len(repo.messages) == 2
    assert repo.messages[0].role == "user"
    assert repo.messages[0].content == "Tell me about this"
    assert repo.messages[1].role == "assistant"
    assert repo.messages[1].content == "Test response."


@pytest.mark.asyncio
async def test_stream_chat_reuses_existing_conversation() -> None:
    """Providing a conversation_id should reuse that conversation."""
    records = [_build_record("r1")]
    repo = FakeConversationRepo(records=records)
    existing_conv = FakeConversation(title="Previous chat")
    repo.conversations[str(existing_conv.id)] = existing_conv
    llm = FakeLLMClient()

    service = ChatService(conversation_repo=repo, llm_client=llm)

    request = ChatRequest(
        search_id="s1",
        message="Follow up question",
        conversation_id=str(existing_conv.id),
    )
    events = [event async for event in service.stream_chat(request)]

    started = next(e for e in events if e.event == "chat_started")
    assert started.data["conversation_id"] == str(existing_conv.id)


@pytest.mark.asyncio
async def test_stream_chat_error_on_missing_search() -> None:
    """Should emit error event if search session has no results."""
    repo = FakeConversationRepo(records=[])
    llm = FakeLLMClient()

    service = ChatService(conversation_repo=repo, llm_client=llm)

    request = ChatRequest(search_id="nonexistent", message="Hello")
    events = [event async for event in service.stream_chat(request)]

    assert any(e.event == "error" for e in events)


@pytest.mark.asyncio
async def test_stream_chat_error_on_missing_conversation() -> None:
    """Should emit error if referenced conversation_id doesn't exist."""
    records = [_build_record("r1")]
    repo = FakeConversationRepo(records=records)
    llm = FakeLLMClient()

    service = ChatService(conversation_repo=repo, llm_client=llm)

    request = ChatRequest(
        search_id="s1",
        message="Hello",
        conversation_id="nonexistent-conv-id",
    )
    events = [event async for event in service.stream_chat(request)]

    assert any(e.event == "error" for e in events)


@pytest.mark.asyncio
async def test_stream_chat_includes_records_context_in_llm_messages() -> None:
    """LLM should receive search context in its system prompt."""
    records = [
        _build_record("r1", "Important Study About Cancer Treatment", ["Smith J"], 2023),
    ]
    repo = FakeConversationRepo(records=records)
    llm = FakeLLMClient()

    service = ChatService(conversation_repo=repo, llm_client=llm)

    request = ChatRequest(search_id="s1", message="Tell me about this paper")
    _ = [event async for event in service.stream_chat(request)]

    assert llm.last_messages
    system_msg = llm.last_messages[0]
    assert system_msg["role"] == "system"
    assert "Important Study About Cancer Treatment" in system_msg["content"]
