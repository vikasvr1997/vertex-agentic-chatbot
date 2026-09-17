from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agentic_chatbot.api import deps
from agentic_chatbot.api.server import app
from agentic_chatbot.core.conversation import ConversationManager


class _FakeVertexClient:
    def send_message(self, session_id: str, message: str) -> str:
        return f"fake-reply: {message}"

    def reset_session(self, session_id: str) -> None:
        return None


@pytest.fixture
def client() -> TestClient:
    # Return the *same* instances on every call — FastAPI invokes an
    # override once per request, so a lambda that constructs a fresh
    # ConversationManager() each time would silently drop session state
    # between requests within one test.
    fake_vertex_client = _FakeVertexClient()
    conversation_manager = ConversationManager()
    app.dependency_overrides[deps.get_vertex_client] = lambda: fake_vertex_client
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
