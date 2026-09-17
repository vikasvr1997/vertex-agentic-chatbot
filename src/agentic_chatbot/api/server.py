"""FastAPI application factory — the single backend shared by both the
Streamlit and Chainlit frontends.
"""

from __future__ import annotations

from fastapi import FastAPI

from agentic_chatbot.api.routes.chat import router as chat_router
from agentic_chatbot.api.routes.schema import router as schema_router
from agentic_chatbot.api.routes.status import router as status_router
from agentic_chatbot.api.schemas import HealthResponse
from agentic_chatbot.config import get_settings
from agentic_chatbot.logging_config import configure_logging

configure_logging()

app = FastAPI(
    title="Agentic Chatbot API",
    description=(
        "Internal backend for an enterprise conversational agent. "
        "Connects to Vertex AI exclusively via Application Default "
        "Credentials — no API keys are accepted or stored."
    ),
    version="0.1.0",
)

app.include_router(chat_router)
app.include_router(status_router)
app.include_router(schema_router)


@app.get("/health", response_model=HealthResponse, tags=["ops"])
def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(status="ok", environment=settings.environment)
