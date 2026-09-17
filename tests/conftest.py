from __future__ import annotations

import os

import pytest

# Set at import time (module scope), not inside a fixture: conftest.py is
# imported before pytest collects test modules, and some modules
# (agentic_chatbot.api.server) read Settings() at import time via
# configure_logging(). A fixture would run too late to satisfy that.
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test-project")
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "us-central1")
os.environ.setdefault("VERTEX_AGENT_ENGINE_ID", "")
os.environ.setdefault("VERTEX_MODEL_NAME", "gemini-2.5-flash")
os.environ.setdefault("BIGQUERY_DEFAULT_DATASET", "")
os.environ.setdefault("BIGQUERY_MAX_ROWS", "200")
os.environ.setdefault("BIGQUERY_QUERY_TIMEOUT_SECONDS", "30")
os.environ.setdefault("BIGQUERY_MAX_BYTES_BILLED", "200000000")
os.environ.setdefault("INTERNAL_API_TOKEN", "test-token")
os.environ.setdefault("BACKEND_URL", "http://localhost:8080")
os.environ.setdefault("ENVIRONMENT", "test")


@pytest.fixture(autouse=True)
def _base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset the baseline env vars before/after every test.

    Ensures a test that overrides one of these via monkeypatch (e.g. to set
    VERTEX_AGENT_ENGINE_ID) doesn't leak into the next test.
    """
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    monkeypatch.setenv("VERTEX_AGENT_ENGINE_ID", "")
    monkeypatch.setenv("VERTEX_MODEL_NAME", "gemini-2.5-flash")
    monkeypatch.setenv("BIGQUERY_DEFAULT_DATASET", "")
    monkeypatch.setenv("BIGQUERY_MAX_ROWS", "200")
    monkeypatch.setenv("BIGQUERY_QUERY_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("BIGQUERY_MAX_BYTES_BILLED", "200000000")
    monkeypatch.setenv("INTERNAL_API_TOKEN", "test-token")
    monkeypatch.setenv("BACKEND_URL", "http://localhost:8080")
    monkeypatch.setenv("ENVIRONMENT", "test")

    from agentic_chatbot.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
