"""Central configuration, loaded from environment variables / .env.

No Vertex AI API keys are read here by design: connectivity to Vertex AI
is established exclusively through Application Default Credentials (ADC).
See core.auth.ensure_adc() for the startup credential check.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Google Cloud / Vertex AI
    google_cloud_project: str = Field(alias="GOOGLE_CLOUD_PROJECT")
    google_cloud_location: str = Field(default="us-central1", alias="GOOGLE_CLOUD_LOCATION")
    vertex_agent_engine_id: str = Field(default="", alias="VERTEX_AGENT_ENGINE_ID")
    vertex_model_name: str = Field(default="gemini-2.5-flash", alias="VERTEX_MODEL_NAME")

    # BigQuery data-query agent (read-only). Leave the dataset empty to skip
    # SQL routing entirely — the orchestrator falls straight back to chat.
    bigquery_default_dataset: str = Field(default="", alias="BIGQUERY_DEFAULT_DATASET")
    bigquery_max_rows: int = Field(default=200, alias="BIGQUERY_MAX_ROWS")
    bigquery_query_timeout_seconds: float = Field(
        default=30.0, alias="BIGQUERY_QUERY_TIMEOUT_SECONDS"
    )
    bigquery_max_bytes_billed: int = Field(default=200_000_000, alias="BIGQUERY_MAX_BYTES_BILLED")

    # Internal API
    internal_api_token: str = Field(default="change-me-local-dev-only", alias="INTERNAL_API_TOKEN")
    backend_url: str = Field(default="http://localhost:8080", alias="BACKEND_URL")

    # Observability
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    environment: str = Field(default="local", alias="ENVIRONMENT")

    @property
    def uses_agent_engine(self) -> bool:
        """True once a Vertex AI Agent Engine (Reasoning Engine) has been deployed."""
        return bool(self.vertex_agent_engine_id)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
