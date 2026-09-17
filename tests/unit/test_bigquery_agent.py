from __future__ import annotations

import pytest

from agentic_chatbot.agents.bigquery_agent import BigQueryAgent
from agentic_chatbot.config import get_settings
from agentic_chatbot.services.bigquery_service import ReadOnlyQueryError


class _FakeVertexClient:
    """Returns scripted responses in order — one per generate_once() call.

    BigQueryAgent.answer() calls generate_once() once for SQL generation,
    then again for summarization if the query returned rows. Pass one
    response for a NO_QUERY/error-before-summarization test, two for a
    successful-query test.
    """

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def generate_once(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.responses.pop(0)


class _FakeBigQueryService:
    def __init__(
        self, rows: list[dict[str, object]] | None = None, error: Exception | None = None
    ) -> None:
        self.rows = rows or []
        self.error = error
        self.queries: list[str] = []

    def describe_dataset(self, dataset: str) -> str:
        return f"Table `{dataset}.t`: id (INT64)"

    def run_query(self, sql: str) -> list[dict[str, object]]:
        self.queries.append(sql)
        if self.error:
            raise self.error
        return self.rows


@pytest.fixture(autouse=True)
def _dataset_configured() -> None:
    settings = get_settings()
    settings.bigquery_default_dataset = "my_dataset"
    yield
    get_settings.cache_clear()


def test_bigquery_agent_returns_rows_on_success() -> None:
    vertex_client = _FakeVertexClient(["SELECT id FROM t", "There are 2 ids: 1 and 2."])
    bigquery_service = _FakeBigQueryService(rows=[{"id": 1}, {"id": 2}])
    agent = BigQueryAgent(vertex_client, bigquery_service, get_settings())

    result = agent.answer("how many ids?")

    assert result.generated_query == "SELECT id FROM t"
    assert result.table == [{"id": 1}, {"id": 2}]
    assert result.reply == "There are 2 ids: 1 and 2."
    # Second call is the summarization prompt, grounded in the actual rows.
    assert "how many ids?" in vertex_client.prompts[1]
    assert "SELECT id FROM t" in vertex_client.prompts[1]


def test_bigquery_agent_falls_back_to_row_count_if_summarization_fails() -> None:
    class _RaisingOnSecondCall:
        def __init__(self) -> None:
            self.calls = 0

        def generate_once(self, prompt: str) -> str:
            self.calls += 1
            if self.calls == 1:
                return "SELECT id FROM t"
            raise RuntimeError("model unavailable")

    bigquery_service = _FakeBigQueryService(rows=[{"id": 1}, {"id": 2}])
    agent = BigQueryAgent(_RaisingOnSecondCall(), bigquery_service, get_settings())

    result = agent.answer("how many ids?")

    assert result.reply == "Found 2 row(s)."


def test_bigquery_agent_handles_no_query_response() -> None:
    vertex_client = _FakeVertexClient(["NO_QUERY"])
    bigquery_service = _FakeBigQueryService()
    agent = BigQueryAgent(vertex_client, bigquery_service, get_settings())

    result = agent.answer("what's the weather?")

    assert result.generated_query is None
    assert result.table is None
    assert bigquery_service.queries == []


def test_bigquery_agent_strips_markdown_fences_from_model_output() -> None:
    vertex_client = _FakeVertexClient(["```sql\nSELECT id FROM t\n```", "There is one id: 1."])
    bigquery_service = _FakeBigQueryService(rows=[{"id": 1}])
    agent = BigQueryAgent(vertex_client, bigquery_service, get_settings())

    result = agent.answer("list ids")

    assert result.generated_query == "SELECT id FROM t"


def test_bigquery_agent_surfaces_guardrail_rejection() -> None:
    vertex_client = _FakeVertexClient(["DROP TABLE t"])
    bigquery_service = _FakeBigQueryService(error=ReadOnlyQueryError("nope"))
    agent = BigQueryAgent(vertex_client, bigquery_service, get_settings())

    result = agent.answer("delete everything")

    assert result.table is None
    assert "Refused" in result.reply


def test_bigquery_agent_surfaces_query_failure() -> None:
    vertex_client = _FakeVertexClient(["SELECT id FROM missing_table"])
    bigquery_service = _FakeBigQueryService(error=RuntimeError("table not found"))
    agent = BigQueryAgent(vertex_client, bigquery_service, get_settings())

    result = agent.answer("list ids")

    assert result.table is None
    assert "failed" in result.reply


def test_bigquery_agent_returns_canned_message_for_empty_results() -> None:
    vertex_client = _FakeVertexClient(["SELECT id FROM t WHERE 1=0"])
    bigquery_service = _FakeBigQueryService(rows=[])
    agent = BigQueryAgent(vertex_client, bigquery_service, get_settings())

    result = agent.answer("any ids over a million?")

    assert result.reply == "The query returned no rows."
    assert result.table == []
    # No summarization call for empty results — nothing to summarize.
    assert len(vertex_client.prompts) == 1
