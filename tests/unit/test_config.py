from __future__ import annotations

import pytest

from agentic_chatbot.config import get_settings


def test_settings_load_from_env() -> None:
    settings = get_settings()
    assert settings.google_cloud_project == "test-project"
    assert settings.google_cloud_location == "us-central1"
    assert settings.uses_agent_engine is False


def test_uses_agent_engine_true_when_id_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "VERTEX_AGENT_ENGINE_ID",
        "projects/test-project/locations/us-central1/reasoningEngines/123",
    )
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.uses_agent_engine is True
