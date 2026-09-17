"""Vertex AI connectivity — ADC only, no API keys.

Two backends are supported behind one interface:

* ``ReasoningEngineBackend`` — talks to a deployed Vertex AI Agent Engine
  (Reasoning Engine) once you have built and deployed your agent.
* ``GeminiModelBackend`` — talks directly to a Gemini model in Vertex AI,
  used for local development before an Agent Engine exists.

Both authenticate purely through Application Default Credentials, resolved
by ``vertexai.init()`` / ``google.auth.default()``. Selection between them is
driven by ``Settings.uses_agent_engine`` (i.e. whether
``VERTEX_AGENT_ENGINE_ID`` is set) so switching over later requires no code
changes, only configuration.
"""

from __future__ import annotations

from typing import Protocol

import vertexai
from tenacity import retry, stop_after_attempt, wait_exponential

from agentic_chatbot.config import Settings, get_settings
from agentic_chatbot.core.auth import ensure_adc
from agentic_chatbot.logging_config import get_logger

logger = get_logger(__name__)


class AgentBackend(Protocol):
    def send_message(
        self, session_id: str, message: str, model_override: str | None = None
    ) -> str: ...

    def reset_session(self, session_id: str) -> None: ...


class ReasoningEngineBackend:
    """Talks to a deployed Vertex AI Agent Engine (Reasoning Engine)."""

    def __init__(self, resource_name: str) -> None:
        from vertexai.preview import reasoning_engines

        self._engine = reasoning_engines.ReasoningEngine(resource_name)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def send_message(self, session_id: str, message: str, model_override: str | None = None) -> str:
        # The deployed agent owns its own model choice; a per-request override
        # has no meaning here.
        if model_override:
            logger.warning("model_override_ignored", backend="reasoning_engine")
        response = self._engine.query(input={"session_id": session_id, "message": message})
        return _extract_text(response)

    def reset_session(self, session_id: str) -> None:
        # Session state lives inside the deployed agent; nothing to clear locally.
        return None


class GeminiModelBackend:
    """Direct Gemini model chat — used before an Agent Engine is deployed."""

    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self._sessions: dict[str, object] = {}

    def _session_for(self, session_id: str, model_override: str | None) -> object:
        from vertexai.generative_models import GenerativeModel

        if session_id not in self._sessions:
            model = GenerativeModel(model_override or self._model_name)
            self._sessions[session_id] = model.start_chat()
        return self._sessions[session_id]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def send_message(self, session_id: str, message: str, model_override: str | None = None) -> str:
        chat = self._session_for(session_id, model_override)
        response = chat.send_message(message)  # type: ignore[attr-defined]
        return str(response.text)

    def reset_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


def _extract_text(response: object) -> str:
    if isinstance(response, dict):
        for key in ("output", "response", "text"):
            value = response.get(key)
            if isinstance(value, str):
                return value
    return str(response)


class VertexAgentClient:
    """Facade used by the API layer; picks a backend based on Settings."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        ensure_adc()
        vertexai.init(
            project=self._settings.google_cloud_project,
            location=self._settings.google_cloud_location,
        )
        self._backend: AgentBackend = self._build_backend()
        self._utility_model: object | None = None

    def _build_backend(self) -> AgentBackend:
        if self._settings.uses_agent_engine:
            logger.info("vertex_backend_selected", backend="reasoning_engine")
            return ReasoningEngineBackend(self._settings.vertex_agent_engine_id)
        logger.info("vertex_backend_selected", backend="gemini_model")
        return GeminiModelBackend(self._settings.vertex_model_name)

    def send_message(self, session_id: str, message: str, model_override: str | None = None) -> str:
        return self._backend.send_message(session_id, message, model_override=model_override)

    def reset_session(self, session_id: str) -> None:
        self._backend.reset_session(session_id)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def generate_once(self, prompt: str) -> str:
        """Single-turn, history-free generation — used for classification and
        SQL generation, which must never leak into a user's chat history.

        Always goes through a direct Gemini model call, regardless of whether
        the main conversational backend is an Agent Engine — these are
        auxiliary/internal calls, not the user-facing agent response.
        """
        from vertexai.generative_models import GenerativeModel

        if self._utility_model is None:
            self._utility_model = GenerativeModel(self._settings.vertex_model_name)
        response = self._utility_model.generate_content(prompt)  # type: ignore[union-attr]
        return str(response.text)
