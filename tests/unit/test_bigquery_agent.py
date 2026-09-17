from __future__ import annotations

import pytest

from agentic_chatbot.agents.bigquery_agent import BigQueryAgent
from agentic_chatbot.config import get_settings
from agentic_chatbot.services.bigquery_service import ReadOnlyQueryError


class _FakeVertexClient:
    def __init__(self, sql: str) -> None:
        self.sql = sql
        self.prompts: list[str] = []

    def generate_once(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.sql


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
    vertex_client = _FakeVertexClient("SELECT id FROM t")
    bigquery_service = _FakeBigQueryService(rows=[{"id": 1}, {"id": 2}])
    agent = BigQueryAgent(vertex_client, bigquery_service, get_settings())

    result = agent.answer("how many ids?")

    assert result.generated_query == "SELECT id FROM t"
    assert result.table == [{"id": 1}, {"id": 2}]
    assert "2 row" in result.reply


def test_bigquery_agent_handles_no_query_response() -> None:
    vertex_client = _FakeVertexClient("NO_QUERY")
    bigquery_service = _FakeBigQueryService()
    agent = BigQueryAgent(vertex_client, bigquery_service, get_settings())

    result = agent.answer("what's the weather?")

    assert result.generated_query is None
    assert result.table is None
    assert bigquery_service.queries == []


def test_bigquery_agent_strips_markdown_fences_from_model_output() -> None:
    vertex_client = _FakeVertexClient("```sql\nSELECT id FROM t\n```")
    bigquery_service = _FakeBigQueryService(rows=[{"id": 1}])
    agent = BigQueryAgent(vertex_client, bigquery_service, get_settings())

    result = agent.answer("list ids")

    assert result.generated_query == "SELECT id FROM t"


def test_bigquery_agent_surfaces_guardrail_rejection() -> None:
    vertex_client = _FakeVertexClient("DROP TABLE t")
    bigquery_service = _FakeBigQueryService(error=ReadOnlyQueryError("nope"))
    agent = BigQueryAgent(vertex_client, bigquery_service, get_settings())

    result = agent.answer("delete everything")

    assert result.table is None
    assert "Refused" in result.reply


def test_bigquery_agent_surfaces_query_failure() -> None:
    vertex_client = _FakeVertexClient("SELECT id FROM missing_table")
    bigquery_service = _FakeBigQueryService(error=RuntimeError("table not found"))
    agent = BigQueryAgent(vertex_client, bigquery_service, get_settings())

    result = agent.answer("list ids")

    assert result.table is None
    assert "failed" in result.reply
