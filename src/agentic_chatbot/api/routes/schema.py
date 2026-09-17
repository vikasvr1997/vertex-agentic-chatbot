"""Schema-cache admin route — backs the "Refresh schema" settings action."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from agentic_chatbot.api.deps import BigQueryServiceDep, require_internal_token
from agentic_chatbot.api.schemas import RefreshSchemaResponse

router = APIRouter(prefix="/schema", tags=["ops"], dependencies=[Depends(require_internal_token)])


@router.post("/refresh", response_model=RefreshSchemaResponse)
def refresh_schema(bigquery_service: BigQueryServiceDep) -> RefreshSchemaResponse:
    bigquery_service.clear_schema_cache()
    return RefreshSchemaResponse(status="cleared")
