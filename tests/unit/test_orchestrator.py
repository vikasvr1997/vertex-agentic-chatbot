from __future__ import annotations

import pytest

from agentic_chatbot.agents.bigquery_agent import BigQueryAgentResult
from agentic_chatbot.agents.orchestrator import Orchestrator
from agentic_chatbot.config import get_settings
from agentic_chatbot.core.conversation import Session


class _FakeVertexClient:
    def __init__(self, classification: str) -> None:
        self.classification = classification
        self.prompts: list[str] = []

    def generate_once(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.classification


class _FakeChatAgent:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []

    def reply(self, session_id: str, message: str, model_override: str | None = None) -> str:
        self.calls.append((session_id, message, model_override))
        return f"chat-reply: {message}"


class _FakeBigQueryAgent:
    def __init__(self, result: BigQueryAgentResult, schema_explanation: str = "unused") -> None:
        self.result = result
        self.schema_explanation = schema_explanation
        self.questions: list[str] = []
        self.describe_schema_calls = 0

    def answer(self, question: str) -> BigQueryAgentResult:
        self.questions.append(question)
        return self.result

    def describe_schema(self) -> str:
        self.describe_schema_calls += 1
        return self.schema_explanation


def _make_orchestrator(
    classification: str, bigquery_result: BigQueryAgentResult, dataset: str
) -> tuple[Orchestrator, _FakeChatAgent, _FakeBigQueryAgent]:
    settings = get_settings()
    settings.bigquery_default_dataset = dataset
    vertex_client = _FakeVertexClient(classification)
    chat_agent = _FakeChatAgent()
    bigquery_agent = _FakeBigQueryAgent(bigquery_result)
    orchestrator = Orchestrator(vertex_client, chat_agent, bigquery_agent, settings)
    return orchestrator, chat_agent, bigquery_agent


def test_orchestrator_skips_classification_when_no_dataset_configured() -> None:
    orchestrator, chat_agent, bigquery_agent = _make_orchestrator(
        classification="SQL", bigquery_result=BigQueryAgentResult("x", None, None), dataset=""
    )

    result = orchestrator.handle(Session(session_id="s1"), "how many rows?")

    assert result.reply == "chat-reply: how many rows?"
    assert chat_agent.calls == [("s1", "how many rows?", None)]
    assert bigquery_agent.questions == []


def test_orchestrator_routes_to_bigquery_agent_on_sql_classification() -> None:
    bq_result = BigQueryAgentResult(
        reply="Found 2 row(s).",
        generated_query="SELECT category, count FROM t",
        table=[{"category": "a", "count": 3}, {"category": "b", "count": 5}],
    )
    orchestrator, chat_agent, bigquery_agent = _make_orchestrator(
        classification="SQL", bigquery_result=bq_result, dataset="my_dataset"
    )

    result = orchestrator.handle(Session(session_id="s1"), "how many rows per category?")

    assert result.reply == "Found 2 row(s)."
    assert result.generated_query == "SELECT category, count FROM t"
    assert result.table == bq_result.table
    assert result.chart is not None and result.chart["type"] == "bar"
    assert bigquery_agent.questions == ["how many rows per category?"]
    assert chat_agent.calls == []


def test_orchestrator_routes_to_chat_agent_on_chat_classification() -> None:
    orchestrator, chat_agent, bigquery_agent = _make_orchestrator(
        classification="CHAT",
        bigquery_result=BigQueryAgentResult("x", None, None),
        dataset="my_dataset",
    )

    result = orchestrator.handle(
        Session(session_id="s1"), "hello there", model_override="gemini-2.5-pro"
    )

    assert result.reply == "chat-reply: hello there"
    assert chat_agent.calls == [("s1", "hello there", "gemini-2.5-pro")]
    assert bigquery_agent.questions == []


def test_orchestrator_falls_back_to_chat_when_classification_raises() -> None:
    class _RaisingVertexClient:
        def generate_once(self, prompt: str) -> str:
            raise RuntimeError("boom")

    settings = get_settings()
    settings.bigquery_default_dataset = "my_dataset"
    chat_agent = _FakeChatAgent()
    bigquery_agent = _FakeBigQueryAgent(BigQueryAgentResult("x", None, None))
    orchestrator = Orchestrator(_RaisingVertexClient(), chat_agent, bigquery_agent, settings)

    result = orchestrator.handle(Session(session_id="s1"), "hello")

    assert result.reply == "chat-reply: hello"
    assert bigquery_agent.questions == []


def test_orchestrator_offers_chart_when_none_could_be_inferred() -> None:
    # A single scalar row (e.g. "how many drivers?") has nothing chartable.
    bq_result = BigQueryAgentResult(
        reply="There are 150 drivers.",
        generated_query="SELECT COUNT(*) AS n FROM drivers",
        table=[{"n": 150}],
    )
    orchestrator, _, _ = _make_orchestrator(
        classification="SQL", bigquery_result=bq_result, dataset="my_dataset"
    )
    session = Session(session_id="s1")

    result = orchestrator.handle(session, "how many drivers?")

    assert "Would you like me to generate a sample chart" in result.reply
    assert result.chart is None
    assert session.pending_chart_offer is True
    assert session.last_query_rows == [{"n": 150}]


def test_orchestrator_does_not_offer_chart_when_one_was_already_shown() -> None:
    bq_result = BigQueryAgentResult(
        reply="Found 2 row(s).",
        generated_query="SELECT category, count FROM t",
        table=[{"category": "a", "count": 3}, {"category": "b", "count": 5}],
    )
    orchestrator, _, _ = _make_orchestrator(
        classification="SQL", bigquery_result=bq_result, dataset="my_dataset"
    )
    session = Session(session_id="s1")

    result = orchestrator.handle(session, "counts by category")

    assert "Would you like" not in result.reply
    assert result.chart is not None
    assert session.pending_chart_offer is False


def test_orchestrator_builds_chart_on_affirmative_followup() -> None:
    bq_result = BigQueryAgentResult(reply="unused", generated_query="unused", table=[{"n": 1}])
    orchestrator, chat_agent, bigquery_agent = _make_orchestrator(
        classification="SQL", bigquery_result=bq_result, dataset="my_dataset"
    )
    session = Session(
        session_id="s1",
        pending_chart_offer=True,
        last_query_rows=[{"category": "a", "count": 3}, {"category": "b", "count": 5}],
        last_query_sql="SELECT category, count FROM t",
    )

    result = orchestrator.handle(session, "yes please")

    assert result.chart is not None
    assert result.chart["type"] == "bar"
    assert result.table == [{"category": "a", "count": 3}, {"category": "b", "count": 5}]
    assert result.generated_query == "SELECT category, count FROM t"
    assert session.pending_chart_offer is False
    assert session.last_query_rows is None
    # The follow-up is fully handled without touching classification or the
    # BigQuery agent again.
    assert chat_agent.calls == []
    assert bigquery_agent.questions == []


def test_orchestrator_explains_when_followup_data_still_not_chartable() -> None:
    orchestrator, _, _ = _make_orchestrator(
        classification="SQL",
        bigquery_result=BigQueryAgentResult("x", None, None),
        dataset="my_dataset",
    )
    session = Session(session_id="s1", pending_chart_offer=True, last_query_rows=[{"n": 150}])

    result = orchestrator.handle(session, "yes")

    assert "doesn't have enough structure" in result.reply
    assert result.chart is None
    assert session.pending_chart_offer is False


def test_orchestrator_routes_to_schema_explanation_on_schema_classification() -> None:
    orchestrator, chat_agent, bigquery_agent = _make_orchestrator(
        classification="SCHEMA",
        bigquery_result=BigQueryAgentResult("unused", None, None),
        dataset="my_dataset",
    )
    bigquery_agent.schema_explanation = "This dataset has a `drivers` table with driver_id, name."

    result = orchestrator.handle(Session(session_id="s1"), "explain the dataset")

    assert result.reply == "This dataset has a `drivers` table with driver_id, name."
    assert result.generated_query is None
    assert result.table is None
    assert bigquery_agent.describe_schema_calls == 1
    # A schema question never touches SQL generation or the chat model.
    assert bigquery_agent.questions == []
    assert chat_agent.calls == []


def test_orchestrator_treats_non_affirmative_followup_as_a_new_message() -> None:
    orchestrator, chat_agent, _ = _make_orchestrator(
        classification="CHAT",
        bigquery_result=BigQueryAgentResult("x", None, None),
        dataset="my_dataset",
    )
    session = Session(session_id="s1", pending_chart_offer=True, last_query_rows=[{"n": 150}])

    result = orchestrator.handle(session, "no thanks, what's the capital of France?")

    assert result.reply == "chat-reply: no thanks, what's the capital of France?"
    assert session.pending_chart_offer is False
    assert chat_agent.calls == [("s1", "no thanks, what's the capital of France?", None)]


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> None:
    yield
    get_settings.cache_clear()
