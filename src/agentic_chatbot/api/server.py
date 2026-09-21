"""FastAPI application factory — the single backend shared by both the
Streamlit and Chainlit frontends.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agentic_chatbot.api.routes.chat import router as chat_router
from agentic_chatbot.api.routes.schema import router as schema_router
from agentic_chatbot.api.routes.status import router as status_router
from agentic_chatbot.api.schemas import HealthResponse
from agentic_chatbot.config import get_settings
from agentic_chatbot.devtools.architecture_flow_tool import api_router as _flow_tool_api_router
from agentic_chatbot.devtools.architecture_flow_tool import dash_app as _flow_tool_dash_app
from agentic_chatbot.logging_config import configure_logging

try:
    from a2wsgi import WSGIMiddleware
except ImportError:  # pragma: no cover - fallback to Starlette's deprecated bridge
    from starlette.middleware.wsgi import WSGIMiddleware

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

# --- Developer-only debugging tool: architecture flow visualizer ----------
# Mounted per its own module docstring's merge instructions. Dash's Flask
# server is bridged onto this same ASGI app/port via a2wsgi — no second
# server or port. Purely a dev aid; not part of the chat product above.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(_flow_tool_api_router, prefix="/architecture-tool/api")
app.mount("/architecture-tool", WSGIMiddleware(_flow_tool_dash_app.server))


@app.get("/health", response_model=HealthResponse, tags=["ops"])
def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(status="ok", environment=settings.environment)
