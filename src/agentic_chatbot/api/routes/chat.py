"""Chat endpoint — the only authenticated business route in the backend."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from agentic_chatbot.api.deps import (
    get_conversation_manager,
    get_vertex_client,
    require_internal_token,
)
from agentic_chatbot.api.schemas import ChatRequest, ChatResponse
from agentic_chatbot.core.conversation import ConversationManager
from agentic_chatbot.core.vertex_client import VertexAgentClient
from agentic_chatbot.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(require_internal_token)])


@router.post("", response_model=ChatResponse)
def send_chat_message(
    payload: ChatRequest,
    conversations: ConversationManager = Depends(get_conversation_manager),
    vertex_client: VertexAgentClient = Depends(get_vertex_client),
) -> ChatResponse:
    session = conversations.get_or_create(payload.session_id)
    reply = vertex_client.send_message(session.session_id, payload.message)
    conversations.touch(session.session_id)
    logger.info("chat_turn_completed", session_id=session.session_id, turn=session.turn_count)
    return ChatResponse(session_id=session.session_id, reply=reply, turn_count=session.turn_count)
