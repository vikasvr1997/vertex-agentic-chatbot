"""Routes each user message to the chat agent or the BigQuery agent.

Classification is a stateless, single-turn Gemini call (never mixed into
the user's conversation history) rather than keyword matching, so it
generalizes past a fixed phrase list. If no BigQuery dataset is configured,
classification is skipped entirely and every message goes to chat — this
keeps the app usable out of the box with Vertex AI alone.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentic_chatbot.agents.bigquery_agent import BigQueryAgent
from agentic_chatbot.agents.chat_agent import ChatAgent
from agentic_chatbot.config import Settings
from agentic_chatbot.core.vertex_client import VertexAgentClient
from agentic_chatbot.logging_config import get_logger
from agentic_chatbot.services.chart_utils import infer_chart

logger = get_logger(__name__)

_CLASSIFY_PROMPT = """Classify the user's message as exactly one word: SQL or CHAT.

SQL: the message asks a question that requires querying structured data — \
counts, aggregates, "show me", "how many", "top N", "list", "average", \
filtering or grouping records.
CHAT: anything else — greetings, general questions, requests unrelated to \
querying data.

Message: {message}
Answer:"""


@dataclass
class OrchestratorResult:
    reply: str
    generated_query: str | None = None
    table: list[dict[str, object]] | None = None
    chart: dict[str, object] | None = None


class Orchestrator:
    def __init__(
        self,
        vertex_client: VertexAgentClient,
        chat_agent: ChatAgent,
        bigquery_agent: BigQueryAgent,
        settings: Settings,
    ) -> None:
        self._vertex_client = vertex_client
        self._chat_agent = chat_agent
        self._bigquery_agent = bigquery_agent
        self._settings = settings

    def handle(
        self, session_id: str, message: str, model_override: str | None = None
    ) -> OrchestratorResult:
        if not self._settings.bigquery_default_dataset:
            reply = self._chat_agent.reply(session_id, message, model_override=model_override)
            return OrchestratorResult(reply=reply)

        if self._classify(message) == "SQL":
            result = self._bigquery_agent.answer(message)
            chart = infer_chart(result.table) if result.table else None
            return OrchestratorResult(
                reply=result.reply,
                generated_query=result.generated_query,
                table=result.table,
                chart=chart,
            )

        reply = self._chat_agent.reply(session_id, message, model_override=model_override)
        return OrchestratorResult(reply=reply)

    def _classify(self, message: str) -> str:
        try:
            raw = self._vertex_client.generate_once(_CLASSIFY_PROMPT.format(message=message))
        except Exception as exc:  # noqa: BLE001 — fall back to chat on any classification error
            logger.warning("intent_classification_failed", error=str(exc))
            return "CHAT"
        return "SQL" if "SQL" in raw.upper() else "CHAT"
