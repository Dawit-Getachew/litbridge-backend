"""FastAPI router for conversational chat over search results."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from src.api.v1.sse import stream_generator
from src.core.deps import get_chat_service, get_conversation_repo, get_current_user_optional
from src.models.user import User
from src.repositories.conversation_repo import ConversationRepository
from src.schemas.chat import (
    ChatRequest,
    ConversationHistoryResponse,
    ConversationResponse,
    MessageResponse,
)
from src.services.chat_service import ChatService

router = APIRouter(tags=["Chat"])


@router.post("/chat/stream")
async def stream_chat(
    request: ChatRequest,
    service: ChatService = Depends(get_chat_service),
    user: User | None = Depends(get_current_user_optional),
) -> StreamingResponse:
    """Stream a conversational AI response about search results."""
    return StreamingResponse(
        stream_generator(service.stream_chat(request)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.get(
    "/chat/{conversation_id}/history",
    response_model=ConversationHistoryResponse,
)
async def get_conversation_history(
    conversation_id: str,
    repo: ConversationRepository = Depends(get_conversation_repo),
    user: User | None = Depends(get_current_user_optional),
) -> ConversationHistoryResponse:
    """Retrieve full conversation with messages."""
    conversation = await repo.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    return ConversationHistoryResponse(
        conversation=ConversationResponse(
            id=str(conversation.id),
            search_id=str(conversation.search_session_id),
            title=conversation.title,
            message_count=conversation.message_count,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
        ),
        messages=[
            MessageResponse(
                id=str(msg.id),
                role=msg.role,
                content=msg.content,
                record_ids=msg.record_ids,
                created_at=msg.created_at,
            )
            for msg in conversation.messages
        ],
    )


@router.get(
    "/chat/conversations/{search_id}",
    response_model=list[ConversationResponse],
)
async def list_conversations(
    search_id: str,
    repo: ConversationRepository = Depends(get_conversation_repo),
    user: User | None = Depends(get_current_user_optional),
) -> list[ConversationResponse]:
    """List all conversation threads for a search session."""
    conversations = await repo.list_conversations(search_id)
    return [
        ConversationResponse(
            id=str(conv.id),
            search_id=str(conv.search_session_id),
            title=conv.title,
            message_count=conv.message_count,
            created_at=conv.created_at,
            updated_at=conv.updated_at,
        )
        for conv in conversations
    ]
