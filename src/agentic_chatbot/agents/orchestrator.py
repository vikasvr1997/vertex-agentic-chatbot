"""Routes each user message to the chat agent, the BigQuery agent, or a
direct schema explanation.

Classification is a stateless, single-turn Gemini call (never mixed into
the user's conversation history) rather than keyword matching, so it
generalizes past a fixed phrase list. It distinguishes three intents:

* SQL — answerable by generating and running a query (``BigQueryAgent.answer``).
* SCHEMA — a meta-question about the dataset itself ("what data do you
  have?", "explain the dataset") — answered directly from the live schema
  (``BigQueryAgent.describe_schema``), never as a SELECT statement and
  never by the schema-blind general chat model.
* CHAT — everything else, handled by ``ChatAgent``.

If no BigQuery dataset is configured, classification is skipped entirely
and every message goes to chat — this keeps the app usable out of the box
with Vertex AI alone.

When a BigQuery query returns rows but nothing chartable was found (see
``services.chart_utils.infer_chart``), the reply is appended with a
follow-up offer ("would you like a chart?") and the rows are remembered on
the session. The *next* message is checked for an affirmative reply before
falling through to normal classification — this is a simple keyword
heuristic, not another model call, since it only has to distinguish
"yes" from "anything else".
"""

from __future__ import annotations

from dataclasses import dataclass

from agentic_chatbot.agents.bigquery_agent import BigQueryAgent
from agentic_chatbot.agents.chat_agent import ChatAgent
from agentic_chatbot.config import Settings
from agentic_chatbot.core.conversation import Session
from agentic_chatbot.core.vertex_client import VertexAgentClient
from agentic_chatbot.logging_config import get_logger
from agentic_chatbot.services.chart_utils import infer_chart

logger = get_logger(__name__)

_CLASSIFY_PROMPT = """Classify the user's message as exactly one word: SQL, SCHEMA, or CHAT.

SQL: the message asks a question that requires querying structured data — \
counts, aggregates, "show me", "how many", "top N", "list", "average", \
filtering or grouping records.
SCHEMA: the message asks about the dataset itself, not a query result — \
"what tables/data do you have", "explain the dataset", "describe the data", \
"what's in the database".
CHAT: anything else — greetings, general questions, requests unrelated to \
querying data.

Message: {message}
Answer:"""

_CHART_OFFER_TEXT = "\n\nWould you like me to generate a sample chart for this data?"

_AFFIRMATIVE_PHRASES = {
    "y",
    "yes",
    "yeah",
    "yep",
    "yup",
    "sure",
    "ok",
    "okay",
    "please",
    "please do",
    "go ahead",
    "do it",
}
_AFFIRMATIVE_KEYWORDS = ("yes", "yeah", "yep", "yup", "sure", "please", "chart", "plot", "graph")


def _is_affirmative(message: str) -> bool:
    normalized = message.strip().lower().rstrip(".!")
    if normalized in _AFFIRMATIVE_PHRASES:
        return True
    return any(keyword in normalized for keyword in _AFFIRMATIVE_KEYWORDS)


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
        self, session: Session, message: str, model_override: str | None = None
    ) -> OrchestratorResult:
        if session.pending_chart_offer:
            session.pending_chart_offer = False
            if _is_affirmative(message):
                return self._followup_chart(session)
            # Not an answer to the offer — treat as a fresh message below.

        if not self._settings.bigquery_default_dataset:
            reply = self._chat_agent.reply(
                session.session_id, message, model_override=model_override
            )
            return OrchestratorResult(reply=reply)

        classification = self._classify(message)
        if classification == "SCHEMA":
            return OrchestratorResult(reply=self._bigquery_agent.describe_schema())
        if classification == "SQL":
            return self._handle_sql(session, message)

        reply = self._chat_agent.reply(session.session_id, message, model_override=model_override)
        return OrchestratorResult(reply=reply)

    def _handle_sql(self, session: Session, message: str) -> OrchestratorResult:
        result = self._bigquery_agent.answer(message)
        chart = infer_chart(result.table) if result.table else None
        reply = result.reply

        if result.table and chart is None:
            session.pending_chart_offer = True
            session.last_query_rows = result.table
            session.last_query_sql = result.generated_query
            reply = f"{reply}{_CHART_OFFER_TEXT}"

        return OrchestratorResult(
            reply=reply,
            generated_query=result.generated_query,
            table=result.table,
            chart=chart,
        )

    def _followup_chart(self, session: Session) -> OrchestratorResult:
        rows = session.last_query_rows
        sql = session.last_query_sql
        session.last_query_rows = None
        session.last_query_sql = None

        if not rows:
            return OrchestratorResult(
                reply="I don't have a previous result to chart anymore — ask me a data question first."
            )

        chart = infer_chart(rows)
        if chart is None:
            return OrchestratorResult(
                reply=(
                    "That result doesn't have enough structure to chart (e.g. it's a "
                    "single value) — try a question that returns multiple rows or categories."
                )
            )

        return OrchestratorResult(
            reply="Here's a chart of that data.", generated_query=sql, table=rows, chart=chart
        )

    def _classify(self, message: str) -> str:
        try:
            raw = self._vertex_client.generate_once(_CLASSIFY_PROMPT.format(message=message))
        except Exception as exc:  # noqa: BLE001 — fall back to chat on any classification error
            logger.warning("intent_classification_failed", error=str(exc))
            return "CHAT"
        normalized = raw.upper()
        if "SCHEMA" in normalized:
            return "SCHEMA"
        if "SQL" in normalized:
            return "SQL"
        return "CHAT"
