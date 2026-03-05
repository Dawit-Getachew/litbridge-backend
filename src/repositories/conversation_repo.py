"""Repository for conversation and message persistence."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.conversation import Conversation, Message
from src.models.search import SearchSession
from src.schemas.records import UnifiedRecord


class ConversationRepository:
    """Persist and retrieve chat conversations and messages."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_conversation(
        self,
        search_session_id: str,
        title: str | None = None,
    ) -> Conversation:
        """Create a new conversation thread for a search session."""
        session_uuid = self._parse_uuid(search_session_id)
        conversation = Conversation(
            search_session_id=session_uuid,
            title=title,
            message_count=0,
        )
        self.db.add(conversation)
        await self.db.commit()
        await self.db.refresh(conversation)
        return conversation

    async def get_conversation(self, conversation_id: str) -> Conversation | None:
        """Load a conversation with its messages."""
        conv_uuid = self._parse_uuid(conversation_id)
        if conv_uuid is None:
            return None

        stmt = (
            select(Conversation)
            .where(Conversation.id == conv_uuid)
            .options(selectinload(Conversation.messages))
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_conversations(self, search_session_id: str) -> list[Conversation]:
        """List all conversations for a search session, newest first."""
        session_uuid = self._parse_uuid(search_session_id)
        if session_uuid is None:
            return []

        stmt = (
            select(Conversation)
            .where(Conversation.search_session_id == session_uuid)
            .order_by(Conversation.created_at.desc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        record_ids: list[str] | None = None,
    ) -> Message:
        """Append a message to a conversation and bump the message count."""
        conv_uuid = self._parse_uuid(conversation_id)
        message = Message(
            conversation_id=conv_uuid,
            role=role,
            content=content,
            record_ids=record_ids,
        )
        self.db.add(message)

        stmt = select(Conversation).where(Conversation.id == conv_uuid)
        conversation = (await self.db.execute(stmt)).scalar_one_or_none()
        if conversation is not None:
            conversation.message_count = (conversation.message_count or 0) + 1

        await self.db.commit()
        await self.db.refresh(message)
        return message

    async def get_messages(
        self,
        conversation_id: str,
        limit: int = 50,
    ) -> list[Message]:
        """Return messages for a conversation ordered by creation time."""
        conv_uuid = self._parse_uuid(conversation_id)
        if conv_uuid is None:
            return []

        stmt = (
            select(Message)
            .where(Message.conversation_id == conv_uuid)
            .order_by(Message.created_at)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_search_session(self, search_id: str) -> SearchSession | None:
        """Load the search session associated with a conversation."""
        session_uuid = self._parse_uuid(search_id)
        if session_uuid is None:
            return None

        stmt = select(SearchSession).where(SearchSession.id == session_uuid)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_search_records(self, search_id: str) -> list[UnifiedRecord]:
        """Load the stored search results for context injection."""
        session = await self.get_search_session(search_id)
        if session is None or not session.results:
            return []

        return [UnifiedRecord.model_validate(item) for item in session.results]

    @staticmethod
    def _parse_uuid(raw_value: str) -> UUID | None:
        try:
            return UUID(raw_value)
        except (TypeError, ValueError):
            return None
