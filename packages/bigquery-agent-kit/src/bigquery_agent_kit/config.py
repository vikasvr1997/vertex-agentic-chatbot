"""Configuration for a reusable BigQuery ADK agent."""

from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from bigquery_agent_kit.guardrails.sql_guardrail import validate_identifier


class BigQueryAgentConfig(BaseModel):
    """Settings required by the BigQuery agent, independent of any UI.

    Pydantic validates every field on construction, so a negative row cap
    or an empty project ID fails immediately and loudly, not three calls
    deep inside an opaque BigQuery error. Instances are frozen: build a new
    config to change a setting rather than mutating a shared one at runtime.

    ``model`` accepts three shapes:

    * A plain Google model name (e.g. ``"gemini-2.5-flash"``) or Vertex AI
      resource name (``"projects/.../publishers/google/models/..."``) — runs
      on Vertex AI / the Gemini API as usual.
    * A ``"provider/model"`` string (e.g. ``"openai/gpt-4o-mini"``,
      ``"anthropic/claude-sonnet-4-5"``) — routed through any provider
      LiteLLM supports. Requires the ``litellm`` extra:
      ``pip install "bigquery-agent-kit[litellm]"``.
    * A pre-built ``google.adk.models.BaseLlm`` instance, for full control
      (custom credentials, a self-hosted endpoint, etc.).

    This class only reads the environment when you explicitly call
    :meth:`from_env` — constructing it directly never does, so it behaves
    the same whether or not your process happens to have unrelated
    environment variables set.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    google_cloud_project: str = Field(
        min_length=1, description="GCP project that owns the BigQuery dataset."
    )
    bigquery_default_dataset: str = Field(
        description="BigQuery dataset the agent is grounded in. A host "
        "application may legitimately pass '' to mean 'BigQuery disabled'; "
        "the kit itself always expects a real dataset to actually operate."
    )
    google_cloud_location: str = Field(
        default="US",
        min_length=1,
        description="Vertex AI region for the model, e.g. 'us-central1'. Not used "
        "for BigQuery itself — BigQuery resolves each query's region from the "
        "dataset referenced, which may be a different region than this one.",
    )
    model: Any = Field(default="gemini-2.5-flash", description="See class docstring.")
    max_rows: int = Field(default=200, gt=0, le=10_000, description="Row cap per query.")
    max_bytes_billed: int = Field(
        default=200_000_000,
        ge=10_485_760,
        description="Per-query BigQuery cost cap, in bytes billed. BigQuery bills in "
        "whole MB with a 10 MB minimum per query, so this must be >= 10485760 "
        "(matches the official BigQueryToolset's own validation).",
    )
    query_timeout_seconds: float = Field(
        default=30.0, gt=0, description="Per-query wall-clock timeout, in seconds."
    )
    bigquery_tools: tuple[str, ...] | None = Field(
        default=None,
        description="Which BigQueryToolset tool names to expose to the agent. "
        "None (default) uses this kit's focused subset — list_table_ids, "
        "get_table_info, execute_sql — chosen so a small/cheap model isn't "
        "juggling tools it doesn't need (forecasting, anomaly detection, "
        "catalog search, ...). Pass an explicit tuple to widen or narrow it, "
        "e.g. ('execute_sql', 'ask_data_insights').",
    )

    @field_validator("google_cloud_project")
    @classmethod
    def _check_project_id(cls, value: str) -> str:
        validate_identifier(value, kind="project")
        return value

    @field_validator("bigquery_default_dataset")
    @classmethod
    def _check_dataset_id(cls, value: str) -> str:
        if value:  # '' is the host app's documented "BigQuery disabled" sentinel.
            validate_identifier(value, kind="dataset")
        return value

    @classmethod
    def from_env(cls) -> BigQueryAgentConfig:
        """Build configuration from the conventional BigQuery environment variables."""
        project_id = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
        dataset_id = os.getenv("BIGQUERY_DEFAULT_DATASET", "").strip()
        if not project_id:
            raise ValueError("GOOGLE_CLOUD_PROJECT must be configured")
        if not dataset_id:
            raise ValueError("BIGQUERY_DEFAULT_DATASET must be configured")
        tools_env = os.getenv("BIGQUERY_TOOLS", "").strip()
        return cls(
            google_cloud_project=project_id,
            bigquery_default_dataset=dataset_id,
            google_cloud_location=os.getenv("GOOGLE_CLOUD_LOCATION", "US"),
            model=os.getenv("BIGQUERY_AGENT_MODEL", "gemini-2.5-flash"),
            max_rows=int(os.getenv("BIGQUERY_MAX_ROWS", "200")),
            max_bytes_billed=int(os.getenv("BIGQUERY_MAX_BYTES_BILLED", "200000000")),
            query_timeout_seconds=float(os.getenv("BIGQUERY_QUERY_TIMEOUT_SECONDS", "30")),
            bigquery_tools=(
                tuple(name.strip() for name in tools_env.split(",") if name.strip())
                if tools_env
                else None
            ),
        )
