"""General conversational agent — a thin wrapper over VertexAgentClient's
stateful chat session, kept separate from the BigQuery agent so the
orchestrator can route between them uniformly.
"""

from __future__ import annotations

from agentic_chatbot.core.vertex_client import VertexAgentClient


class ChatAgent:
    def __init__(self, vertex_client: VertexAgentClient) -> None:
        self._vertex_client = vertex_client

    def reply(self, session_id: str, message: str, model_override: str | None = None) -> str:
        return self._vertex_client.send_message(session_id, message, model_override=model_override)
