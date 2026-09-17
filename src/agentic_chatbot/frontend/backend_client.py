"""Thin HTTP client shared by the Streamlit and Chainlit frontends.

Both UIs talk to the FastAPI backend over HTTP rather than importing
Vertex AI directly — this keeps credential handling and agent logic in one
place and lets either frontend be swapped or scaled independently.
"""

from __future__ import annotations

import httpx

from agentic_chatbot.config import get_settings


def send_message(message: str, session_id: str | None) -> dict[str, object]:
    settings = get_settings()
    response = httpx.post(
        f"{settings.backend_url}/chat",
        json={"message": message, "session_id": session_id},
        headers={"Authorization": f"Bearer {settings.internal_api_token}"},
        timeout=60.0,
    )
    response.raise_for_status()
    return response.json()
