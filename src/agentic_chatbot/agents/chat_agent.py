"""General conversational agent implemented with Google ADK."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass

from google.adk.agents import Agent
from google.adk.models.google_llm import Gemini
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import Client, types

from agentic_chatbot.config import Settings, get_settings
from agentic_chatbot.core.auth import ensure_adc

_APP_NAME = "agentic_chatbot"
_USER_ID = "local-user"


@dataclass
class _AgentRuntime:
    runner: Runner
    app_name: str
    session_service: InMemorySessionService


class ChatAgent:
    """ADK-backed chat runtime; session history is owned by ADK."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        ensure_adc()
        self._runtimes: dict[str, _AgentRuntime] = {}
        self._session_models: dict[str, str] = {}
        self._lock = threading.RLock()

    def reply(self, session_id: str, message: str, model_override: str | None = None) -> str:
        with self._lock:
            model_name = self._session_models.get(
                session_id, model_override or self._settings.vertex_model_name
            )
            runtime = self._runtime_for(model_name)
            sessions = runtime.session_service
            if (
                sessions.get_session_sync(
                    app_name=runtime.app_name,
                    user_id=_USER_ID,
                    session_id=session_id,
                )
                is None
            ):
                sessions.create_session_sync(
                    app_name=runtime.app_name,
                    user_id=_USER_ID,
                    session_id=session_id,
                )
            self._session_models[session_id] = model_name

        message_content = types.Content(
            role="user",
            parts=[types.Part(text=message)],
        )
        final_text = ""
        for event in runtime.runner.run(
            user_id=_USER_ID,
            session_id=session_id,
            new_message=message_content,
        ):
            if not event.is_final_response():
                continue
            content = getattr(event, "content", None) or getattr(event, "message", None)
            parts = getattr(content, "parts", None) or []
            final_text = "".join(
                part.text or "" for part in parts if getattr(part, "text", None)
            ).strip()
        return final_text or "I couldn't produce a response. Please try again."

    def _runtime_for(self, model_name: str) -> _AgentRuntime:
        runtime = self._runtimes.get(model_name)
        if runtime is not None:
            return runtime

        safe_model = re.sub(r"[^a-zA-Z0-9_-]", "_", model_name)
        app_name = f"{_APP_NAME}_{safe_model}"
        agent = Agent(
            name="general_chat_agent",
            model=Gemini(
                model=model_name,
                client=Client(
                    vertexai=True,
                    project=self._settings.google_cloud_project,
                    location=self._settings.google_cloud_location,
                ),
            ),
            description="Handles general conversational questions outside BigQuery analytics.",
            instruction=(
                "You are a concise, helpful assistant. Do not claim to have queried "
                "BigQuery; structured data requests are handled by a separate read-only "
                "data workflow. Be clear when you do not know an answer."
            ),
            generate_content_config=types.GenerateContentConfig(
                temperature=0.3,
                max_output_tokens=1024,
            ),
        )
        app_name = f"{_APP_NAME}_{safe_model}"
        session_service = InMemorySessionService()
        runtime = _AgentRuntime(
            runner=Runner(
                agent=agent,
                app_name=app_name,
                session_service=session_service,
            ),
            app_name=app_name,
            session_service=session_service,
        )
        self._runtimes[model_name] = runtime
        return runtime
