"""Thin HTTP client shared by the Streamlit and Chainlit frontends.

Both UIs talk to the FastAPI backend over HTTP rather than importing
Vertex AI directly — this keeps credential handling and agent logic in one
place and lets either frontend be swapped or scaled independently.
"""

from __future__ import annotations

from typing import Any

import httpx

from agentic_chatbot.config import get_settings


def send_message(
    message: str,
    session_id: str | None,
    model_override: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    payload: dict[str, Any] = {"message": message, "session_id": session_id}
    if model_override:
        payload["settings"] = {"model": model_override}

    response = httpx.post(
        f"{settings.backend_url}/chat",
        json=payload,
        headers={"Authorization": f"Bearer {settings.internal_api_token}"},
        timeout=60.0,
    )
    response.raise_for_status()
    return response.json()


def get_status() -> dict[str, Any] | None:
    """Best-effort fetch of non-secret backend config for the sidebar/settings
    panel. Returns None if the backend isn't reachable yet, rather than
    raising — the chat UI should still render."""
    settings = get_settings()
    try:
        response = httpx.get(
            f"{settings.backend_url}/status",
            headers={"Authorization": f"Bearer {settings.internal_api_token}"},
            timeout=5.0,
        )
        response.raise_for_status()
        return dict(response.json())
    except httpx.HTTPError:
        return None
