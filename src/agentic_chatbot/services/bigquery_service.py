"""Application adapter for the reusable BigQuery agent kit.

The standalone package owns query validation and execution. This adapter keeps
the chatbot's environment settings and ADC preflight behavior at the app edge.
"""

from __future__ import annotations

from typing import Any

from bigquery_agent_kit.config import BigQueryAgentConfig
from bigquery_agent_kit.exceptions import QueryTooExpensiveError, ReadOnlyQueryError
from bigquery_agent_kit.guardrails import validate_read_only
from bigquery_agent_kit.services.bigquery_service import (
    BigQueryService as ReusableBigQueryService,
)

from agentic_chatbot.config import Settings
from agentic_chatbot.core.auth import ensure_bigquery_access


class BigQueryService(ReusableBigQueryService):
    """Map the chatbot Settings object to the reusable BigQuery kit service."""

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        if client is None and settings.bigquery_default_dataset:
            ensure_bigquery_access(
                settings.google_cloud_project,
                settings.bigquery_default_dataset,
            )
        config = BigQueryAgentConfig(
            google_cloud_project=settings.google_cloud_project,
            bigquery_default_dataset=settings.bigquery_default_dataset,
            google_cloud_location=settings.google_cloud_location,
            model=settings.analytics_model_name,
            max_rows=settings.bigquery_max_rows,
            max_bytes_billed=settings.bigquery_max_bytes_billed,
            query_timeout_seconds=settings.bigquery_query_timeout_seconds,
        )
        super().__init__(config, client=client)


__all__ = [
    "BigQueryService",
    "QueryTooExpensiveError",
    "ReadOnlyQueryError",
    "validate_read_only",
]
