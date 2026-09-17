"""FastAPI dependencies: bearer-token auth and shared singletons.

The Vertex AI client is constructed lazily (on first authenticated /chat
call) rather than at import time, so the API can start, and its /health
endpoint can respond, even in environments where ADC has not been
configured yet (e.g. a fresh CI container).
"""

from __future__ import annotations

import secrets
from functools import lru_cache

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from agentic_chatbot.config import get_settings
from agentic_chatbot.core.conversation import ConversationManager
from agentic_chatbot.core.vertex_client import VertexAgentClient

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
