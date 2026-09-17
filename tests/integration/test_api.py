from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agentic_chatbot.agents.orchestrator import OrchestratorResult
from agentic_chatbot.api import deps
from agentic_chatbot.api.server import app
from agentic_chatbot.core.conversation import ConversationManager


class _FakeOrchestrator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []

    def handle(
        self, session_id: str, message: str, model_override: str | None = None
    ) -> OrchestratorResult:
        self.calls.append((session_id, message, model_override))
        return OrchestratorResult(reply=f"fake-reply: {message}")


@pytest.fixture
def orchestrator() -> _FakeOrchestrator:
    return _FakeOrchestrator()


@pytest.fixture
def client(orchestrator: _FakeOrchestrator) -> TestClient:
    # Return the *same* instances on every call — FastAPI invokes an
    # override once per request, so a lambda that constructs a fresh
    # ConversationManager() each time would silently drop session state
    # between requests within one test.
    conversation_manager = ConversationManager()
    app.dependency_overrides[deps.get_orchestrator] = lambda: orchestrator
    app.dependency_overrides[deps.get_conversation_manager] = lambda: conversation_manager
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_health_endpoint_requires_no_auth(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_chat_endpoint_rejects_missing_token(client: TestClient) -> None:
    response = client.post("/chat", json={"message": "hi"})
    assert response.status_code in (401, 403)


def test_chat_endpoint_rejects_wrong_token(client: TestClient) -> None:
    response = client.post(
        "/chat",
        json={"message": "hi"},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert response.status_code == 401


def test_chat_endpoint_succeeds_with_valid_token(client: TestClient) -> None:
    response = client.post(
        "/chat",
        json={"message": "hi"},
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "fake-reply: hi"
    assert body["turn_count"] == 1
    assert body["session_id"]
    assert body["generated_query"] is None
    assert body["table"] is None
    assert body["chart"] is None


def test_chat_endpoint_reuses_session_id(client: TestClient) -> None:
    headers = {"Authorization": "Bearer test-token"}
    first = client.post("/chat", json={"message": "hi"}, headers=headers).json()
    second = client.post(
        "/chat",
        json={"message": "again", "session_id": first["session_id"]},
        headers=headers,
    ).json()
    assert second["session_id"] == first["session_id"]
    assert second["turn_count"] == 2


def test_chat_endpoint_forwards_model_override(
    client: TestClient, orchestrator: _FakeOrchestrator
) -> None:
    client.post(
        "/chat",
        json={"message": "hi", "settings": {"model": "gemini-2.5-pro"}},
        headers={"Authorization": "Bearer test-token"},
    )
    assert orchestrator.calls[-1][2] == "gemini-2.5-pro"


def test_status_endpoint_requires_auth(client: TestClient) -> None:
    response = client.get("/status")
    assert response.status_code in (401, 403)


def test_status_endpoint_returns_config(client: TestClient) -> None:
    response = client.get("/status", headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 200
    body = response.json()
    assert body["google_cloud_project"] == "test-project"
    assert body["backend_mode"] == "gemini_model"
    assert body["bigquery_dataset"] is None
