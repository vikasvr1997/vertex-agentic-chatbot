"""Chat endpoint — the only authenticated business route in the backend.

Every message is routed through the Orchestrator, which decides between
the general chat agent and the read-only BigQuery agent.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from agentic_chatbot.api.deps import ConversationManagerDep, OrchestratorDep, require_internal_token
from agentic_chatbot.api.schemas import ChatRequest, ChatResponse
from agentic_chatbot.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(require_internal_token)])


@router.post("", response_model=ChatResponse)
def send_chat_message(
    payload: ChatRequest,
    conversations: ConversationManagerDep,
    orchestrator: OrchestratorDep,
) -> ChatResponse:
    session = conversations.get_or_create(payload.session_id)
    model_override = (payload.settings or {}).get("model")

    result = orchestrator.handle(session.session_id, payload.message, model_override=model_override)

    conversations.touch(session.session_id)
    logger.info(
        "chat_turn_completed",
        session_id=session.session_id,
        turn=session.turn_count,
        routed_to_sql=result.generated_query is not None,
    )
    return ChatResponse(
        session_id=session.session_id,
        reply=result.reply,
        turn_count=session.turn_count,
        generated_query=result.generated_query,
        table=result.table,
        chart=result.chart,
    )
