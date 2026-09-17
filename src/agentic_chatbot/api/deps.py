"""FastAPI dependencies: bearer-token auth and shared singletons.

Vertex AI and BigQuery clients are constructed lazily (on first
authenticated call) rather than at import time, so the API can start, and
its /health endpoint can respond, even in environments where ADC has not
been configured yet (e.g. a fresh CI container).
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from agentic_chatbot.agents.bigquery_agent import BigQueryAgent
from agentic_chatbot.agents.chat_agent import ChatAgent
from agentic_chatbot.agents.orchestrator import Orchestrator
from agentic_chatbot.config import get_settings
from agentic_chatbot.core.conversation import ConversationManager
from agentic_chatbot.core.vertex_client import VertexAgentClient
from agentic_chatbot.services.bigquery_service import BigQueryService

_bearer_scheme = HTTPBearer(auto_error=True)


def require_internal_token(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> None:
    settings = get_settings()
    if not secrets.compare_digest(credentials.credentials, settings.internal_api_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


@lru_cache
def get_conversation_manager() -> ConversationManager:
    return ConversationManager()


@lru_cache
def get_vertex_client() -> VertexAgentClient:
    return VertexAgentClient()


@lru_cache
def get_bigquery_service() -> BigQueryService:
    return BigQueryService(get_settings())


@lru_cache
def get_chat_agent() -> ChatAgent:
    return ChatAgent(get_vertex_client())


@lru_cache
def get_bigquery_agent() -> BigQueryAgent:
    return BigQueryAgent(get_vertex_client(), get_bigquery_service(), get_settings())


@lru_cache
def get_orchestrator() -> Orchestrator:
    return Orchestrator(get_vertex_client(), get_chat_agent(), get_bigquery_agent(), get_settings())


# Annotated aliases for route signatures — the modern FastAPI DI style
# (avoids Depends(...) as a mutable-looking default argument value).
ConversationManagerDep = Annotated[ConversationManager, Depends(get_conversation_manager)]
VertexAgentClientDep = Annotated[VertexAgentClient, Depends(get_vertex_client)]
OrchestratorDep = Annotated[Orchestrator, Depends(get_orchestrator)]
BigQueryServiceDep = Annotated[BigQueryService, Depends(get_bigquery_service)]
