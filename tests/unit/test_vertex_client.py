from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentic_chatbot.config import get_settings
from agentic_chatbot.core import vertex_client


@pytest.fixture(autouse=True)
def _no_real_adc(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let these tests touch real Google credentials or network."""
    monkeypatch.setattr(vertex_client, "ensure_adc", lambda: None)
    monkeypatch.setattr(vertex_client.vertexai, "init", lambda **kwargs: None)


class _FakeChatSession:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send_message(self, message: str) -> SimpleNamespace:
        self.messages.append(message)
        return SimpleNamespace(text=f"echo: {message}")


class _FakeGenerativeModel:
    def __init__(self, model_name: str) -> None:
        self.model_name = model_name

    def start_chat(self) -> _FakeChatSession:
        return _FakeChatSession()


class _FakeReasoningEngine:
    def __init__(self, resource_name: str) -> None:
        self.resource_name = resource_name

    def query(self, input: dict[str, str]) -> dict[str, str]:
        return {"output": f"agent-reply for {input['message']}"}


def test_gemini_backend_used_when_no_agent_engine_id(monkeypatch: pytest.MonkeyPatch) -> None:
    import vertexai.generative_models as gm

    monkeypatch.setattr(gm, "GenerativeModel", _FakeGenerativeModel)

    client = vertex_client.VertexAgentClient(get_settings())
    reply = client.send_message("session-1", "hello")

    assert reply == "echo: hello"
    assert isinstance(client._backend, vertex_client.GeminiModelBackend)


def test_reasoning_engine_backend_used_when_agent_engine_id_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vertexai.preview import reasoning_engines

    monkeypatch.setattr(reasoning_engines, "ReasoningEngine", _FakeReasoningEngine)
    monkeypatch.setenv(
        "VERTEX_AGENT_ENGINE_ID",
        "projects/test-project/locations/us-central1/reasoningEngines/123",
    )
    get_settings.cache_clear()

    client = vertex_client.VertexAgentClient(get_settings())
    reply = client.send_message("session-1", "hello")

    assert reply == "agent-reply for hello"
    assert isinstance(client._backend, vertex_client.ReasoningEngineBackend)


def test_gemini_backend_reuses_session(monkeypatch: pytest.MonkeyPatch) -> None:
    import vertexai.generative_models as gm

    monkeypatch.setattr(gm, "GenerativeModel", _FakeGenerativeModel)
    backend = vertex_client.GeminiModelBackend("gemini-2.0-flash-001")

    backend.send_message("s1", "first")
    backend.send_message("s1", "second")

    assert len(backend._sessions) == 1


def test_gemini_backend_reset_session_clears_state() -> None:
    backend = vertex_client.GeminiModelBackend("gemini-2.0-flash-001")
    backend._sessions["s1"] = object()

    backend.reset_session("s1")

    assert "s1" not in backend._sessions
