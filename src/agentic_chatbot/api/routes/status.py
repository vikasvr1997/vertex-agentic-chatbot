"""Authenticated status endpoint for the frontends' sidebar/settings panel.

Deliberately separate from the unauthenticated /health liveness probe:
project/model/dataset names aren't secrets, but there's no reason to hand
them to an unauthenticated caller either.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from agentic_chatbot.api.deps import require_internal_token
from agentic_chatbot.api.schemas import StatusResponse
from agentic_chatbot.config import get_settings

router = APIRouter(prefix="/status", tags=["ops"], dependencies=[Depends(require_internal_token)])


@router.get("", response_model=StatusResponse)
def get_status() -> StatusResponse:
    settings = get_settings()
    return StatusResponse(
        google_cloud_project=settings.google_cloud_project,
        google_cloud_location=settings.google_cloud_location,
        vertex_model_name=settings.vertex_model_name,
        backend_mode="agent_engine" if settings.uses_agent_engine else "gemini_model",
        bigquery_dataset=settings.bigquery_default_dataset or None,
    )
