from __future__ import annotations

import pytest
from google.api_core.exceptions import BadRequest

from agentic_chatbot.agents.bigquery_agent import BigQueryAgent
from agentic_chatbot.config import get_settings
from agentic_chatbot.services.bigquery_service import ReadOnlyQueryError


class _FakeVertexClient:
    """Returns scripted responses in order — one per generate_once() call."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []
        self.model_overrides: list[str | None] = []

    def generate_once(self, prompt: str, model_override: str | None = None) -> str:
        self.prompts.append(prompt)
        self.model_overrides.append(model_override)
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

    def describe_graphs(self, dataset: str) -> str:
        return f"CREATE PROPERTY GRAPH `{dataset}.g` NODE TABLES (t KEY (id))"

    def run_query(self, sql: str) -> list[dict[str, object]]:
        self.queries.append(sql)
        if self.error:
            raise self.error
        return self.rows


@pytest.fixture(autouse=True)
def _dataset_configured() -> None:
    settings = get_settings()
    settings.bigquery_default_dataset = "my_dataset"
    settings.analytics_model_name = "gemini-2.5-flash-lite"
    settings.vertex_model_name = "gemini-2.5-flash"
    yield
    get_settings.cache_clear()


def test_bigquery_agent_returns_rows_on_success() -> None:
    vertex_client = _FakeVertexClient(["SELECT id FROM t", "There are 2 ids: 1 and 2."])
    bigquery_service = _FakeBigQueryService(rows=[{"id": 1}, {"id": 2}])
    agent = BigQueryAgent(vertex_client, bigquery_service, get_settings())

    result = agent.answer("how many ids?")

    assert result.generated_query == "SELECT id FROM t"
    assert result.table == [{"id": 1}, {"id": 2}]
    assert result.query_duration_ms is not None
    assert result.query_duration_ms >= 0
    assert result.reply == "The query returned 2 row(s). Results are shown below."
    assert len(vertex_client.prompts) == 1
    assert vertex_client.model_overrides == [get_settings().analytics_model_name]


def test_bigquery_agent_does_not_call_model_to_summarize_rows() -> None:
    vertex_client = _FakeVertexClient(["SELECT id FROM t"])
    bigquery_service = _FakeBigQueryService(rows=[{"id": 1}, {"id": 2}])
    agent = BigQueryAgent(vertex_client, bigquery_service, get_settings())

    result = agent.answer("how many ids?")

    assert result.reply == "The query returned 2 row(s). Results are shown below."
    assert len(vertex_client.prompts) == 1


def test_result_summary_highlights_grounded_revenue_change_extremes() -> None:
    rows = [
        {
            "month": "2025-01",
            "primary_freight_type": "Retail",
            "percentage_change": 8.25,
        },
        {
            "month": "2025-02",
            "primary_freight_type": "Automotive",
            "percentage_change": -11.5,
        },
    ]

    reply = BigQueryAgent._summarize_rows(rows)

    assert "Largest revenue increase: 2025-01 / Retail (+8.2%)" in reply
    assert "Largest revenue decrease: 2025-02 / Automotive (-11.5%)" in reply


def test_complex_question_escalates_when_fast_query_misses_contract() -> None:
    settings = get_settings()
    fast_sql = "SELECT month, SUM(revenue) AS revenue FROM t GROUP BY month"
    repaired_sql = "WITH monthly AS (SELECT month, category, SUM(revenue) AS revenue FROM t GROUP BY month, category), ranked AS (SELECT month, category, revenue, SAFE_DIVIDE(revenue - LAG(revenue) OVER (PARTITION BY category ORDER BY month), LAG(revenue) OVER (PARTITION BY category ORDER BY month)) * 100 AS percentage_change, RANK() OVER (PARTITION BY month ORDER BY revenue DESC) AS rank_num FROM monthly) SELECT month, category, revenue, percentage_change, rank_num FROM ranked WHERE rank_num <= 3"
    vertex_client = _FakeVertexClient([fast_sql, repaired_sql])
    bigquery_service = _FakeBigQueryService(rows=[{"month": "2025-01", "f0_": 10}])
    agent = BigQueryAgent(vertex_client, bigquery_service, settings)

    result = agent.answer("Show top three categories and percentage change from the previous month")

    assert result.table == [{"month": "2025-01", "f0_": 10}]
    assert result.generated_query == repaired_sql
    assert vertex_client.model_overrides == [
        settings.analytics_model_name,
        settings.vertex_model_name,
    ]


def test_lite_no_query_escalates_to_stronger_model() -> None:
    settings = get_settings()
    vertex_client = _FakeVertexClient(["NO_QUERY", "SELECT id FROM t"])
    bigquery_service = _FakeBigQueryService(rows=[{"id": 1}])
    agent = BigQueryAgent(vertex_client, bigquery_service, settings)

    result = agent.answer("How many records are there?")

    assert result.table == [{"id": 1}]
    assert vertex_client.model_overrides == [
        settings.analytics_model_name,
        settings.vertex_model_name,
    ]


def test_bigquery_bad_request_gets_one_grounded_repair_attempt() -> None:
    class _RetryingBigQueryService(_FakeBigQueryService):
        def __init__(self) -> None:
            super().__init__(rows=[{"id": 1}])
            self.attempts = 0

        def run_query(self, sql: str) -> list[dict[str, object]]:
            self.queries.append(sql)
            self.attempts += 1
            if self.attempts == 1:
                raise BadRequest("Syntax error: invalid query")
            return self.rows

    settings = get_settings()
    vertex_client = _FakeVertexClient(["SELECT broken", "SELECT id FROM t"])
    bigquery_service = _RetryingBigQueryService()
    agent = BigQueryAgent(vertex_client, bigquery_service, settings)

    result = agent.answer("How many ids are there?")

    assert result.generated_query == "SELECT id FROM t"
    assert result.table == [{"id": 1}]
    assert bigquery_service.queries == ["SELECT broken", "SELECT id FROM t"]
    assert vertex_client.model_overrides == [
        settings.analytics_model_name,
        settings.vertex_model_name,
    ]
    assert "Table `my_dataset.t`: id (INT64)" in vertex_client.prompts[1]
    assert "Syntax error: invalid query" in vertex_client.prompts[1]


def test_incomplete_category_query_is_repaired_before_bigquery_execution() -> None:
    bad_sql = "SELECT month, load_type, COUNT(load_id) AS number_of_shipments FROM t"
    corrected_sql = (
        "WITH monthly AS (SELECT DATE_TRUNC(l.load_date, MONTH) AS month, "
        "c.primary_freight_type, SUM(l.revenue) AS total_revenue, "
        "COUNT(DISTINCT l.load_id) AS distinct_load_count "
        "FROM `my_dataset.loads` AS l JOIN `my_dataset.customer_data` AS c "
        "ON l.customer_id = c.customer_id GROUP BY month, c.primary_freight_type), "
        "changes AS (SELECT month, primary_freight_type, total_revenue, distinct_load_count, "
        "SAFE_DIVIDE(total_revenue - LAG(total_revenue) OVER "
        "(PARTITION BY primary_freight_type ORDER BY month), "
        "LAG(total_revenue) OVER (PARTITION BY primary_freight_type ORDER BY month)) "
        "* 100 AS percentage_change, RANK() OVER (PARTITION BY month "
        "ORDER BY total_revenue DESC) AS rank_num FROM monthly) "
        "SELECT month, primary_freight_type, total_revenue, distinct_load_count, "
        "percentage_change, rank_num FROM changes WHERE rank_num <= 3"
    )

    class _FreightSchemaBigQueryService(_FakeBigQueryService):
        def describe_dataset(self, dataset: str) -> str:
            return (
                f"Table `{dataset}.loads`: load_id (STRING), customer_id (STRING), "
                "load_date (DATE), revenue (FLOAT64), load_type (STRING)\n"
                f"Table `{dataset}.customer_data`: customer_id (STRING), "
                "primary_freight_type (STRING)"
            )

    vertex_client = _FakeVertexClient([bad_sql, corrected_sql])
    bigquery_service = _FreightSchemaBigQueryService(rows=[{"primary_freight_type": "Retail"}])
    agent = BigQueryAgent(vertex_client, bigquery_service, get_settings())

    result = agent.answer(
        "For each month, show the top three product categories by revenue, "
        "number of shipments, and percentage change from the previous month."
    )

    assert result.generated_query == corrected_sql
    assert result.table == [{"primary_freight_type": "Retail"}]
    assert bigquery_service.queries == [corrected_sql]
    assert "primary_freight_type" in vertex_client.prompts[0]
    assert "COUNT(DISTINCT loads.load_id)" in vertex_client.prompts[0]
    assert "count distinct load_id" in vertex_client.prompts[1].lower()


def test_freight_monthly_question_uses_grounded_template_without_model_call() -> None:
    class _FreightSchemaBigQueryService(_FakeBigQueryService):
        def describe_dataset(self, requested_dataset: str) -> str:
            return (
                f"Table `{requested_dataset}.loads`: load_id (STRING), "
                "customer_id (STRING), load_date (DATE), revenue (FLOAT64)\n"
                f"Table `{requested_dataset}.customer_data`: customer_id (STRING), "
                "primary_freight_type (STRING)"
            )

    rows = [
        {"month": "2025-01-01", "primary_freight_type": "Retail", "percentage_change": 9.5},
        {
            "month": "2025-02-01",
            "primary_freight_type": "Automotive",
            "percentage_change": -7.25,
        },
    ]
    vertex_client = _FakeVertexClient([])
    bigquery_service = _FreightSchemaBigQueryService(rows=rows)
    agent = BigQueryAgent(vertex_client, bigquery_service, get_settings())

    result = agent.answer(
        "For each month, which three freight categories have the most revenue? "
        "Show shipment counts and percentage change from the previous month."
    )

    assert result.table == rows
    assert vertex_client.prompts == []
    assert len(bigquery_service.queries) == 1
    assert "customer_data` AS c" in bigquery_service.queries[0]
    assert "c.primary_freight_type" in bigquery_service.queries[0]
    assert "COUNT(DISTINCT l.load_id)" in bigquery_service.queries[0]
    assert "LAG(total_revenue)" in bigquery_service.queries[0]
    assert "* 100 AS percentage_change" in bigquery_service.queries[0]
    assert "WHERE revenue_rank <= 3" in bigquery_service.queries[0]
    assert "Largest revenue increase" in result.reply
    assert "Largest revenue decrease" in result.reply


def test_bigquery_agent_handles_no_query_response() -> None:
    vertex_client = _FakeVertexClient(["NO_QUERY", "NO_QUERY"])
    bigquery_service = _FakeBigQueryService()
    agent = BigQueryAgent(vertex_client, bigquery_service, get_settings())

    result = agent.answer("what's the weather?")

    assert result.generated_query is None
    assert result.table is None
    assert bigquery_service.queries == []
    assert vertex_client.model_overrides == [
        "gemini-2.5-flash-lite",
        "gemini-2.5-flash",
    ]


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


def test_answer_graph_returns_rows_on_success() -> None:
    vertex_client = _FakeVertexClient(
        ["SELECT * FROM GRAPH_TABLE(g MATCH (a)-[e]->(b) RETURN a.id)"]
    )
    bigquery_service = _FakeBigQueryService(rows=[{"id": 1}])
    agent = BigQueryAgent(vertex_client, bigquery_service, get_settings())

    result = agent.answer_graph("how is a connected to b?")

    assert result.generated_query == "SELECT * FROM GRAPH_TABLE(g MATCH (a)-[e]->(b) RETURN a.id)"
    assert result.table == [{"id": 1}]
    assert result.reply == "The query returned 1 row(s). Results are shown below."
    assert len(vertex_client.prompts) == 1
    # Grounded in the graph DDL, not the flat-table schema, and never asked
    # to produce plain SQL.
    assert "CREATE PROPERTY GRAPH" in vertex_client.prompts[0]
    assert "GRAPH_TABLE" in vertex_client.prompts[0]


def test_answer_graph_handles_no_query_response() -> None:
    vertex_client = _FakeVertexClient(["NO_QUERY"])
    bigquery_service = _FakeBigQueryService()
    agent = BigQueryAgent(vertex_client, bigquery_service, get_settings())

    result = agent.answer_graph("what's the weather?")

    assert result.generated_query is None
    assert result.table is None
    assert "graph query" in result.reply
    assert bigquery_service.queries == []


def test_answer_graph_returns_canned_message_for_no_matches() -> None:
    vertex_client = _FakeVertexClient(["SELECT * FROM GRAPH_TABLE(g MATCH (a) RETURN a.id)"])
    bigquery_service = _FakeBigQueryService(rows=[])
    agent = BigQueryAgent(vertex_client, bigquery_service, get_settings())

    result = agent.answer_graph("is x connected to y?")

    assert result.reply == "The graph query returned no matches."
    assert result.table == []


def test_answer_graph_surfaces_guardrail_rejection() -> None:
    vertex_client = _FakeVertexClient(["DROP TABLE t"])
    bigquery_service = _FakeBigQueryService(error=ReadOnlyQueryError("nope"))
    agent = BigQueryAgent(vertex_client, bigquery_service, get_settings())

    result = agent.answer_graph("delete everything")

    assert result.table is None
    assert "Refused" in result.reply


def test_describe_schema_returns_model_explanation_grounded_in_live_schema() -> None:
    vertex_client = _FakeVertexClient(["This dataset has one table `t` with an id column."])
    bigquery_service = _FakeBigQueryService()
    agent = BigQueryAgent(vertex_client, bigquery_service, get_settings())

    reply = agent.describe_schema()

    assert reply == "This dataset has one table `t` with an id column."
    # Grounded in the actual schema text, not asked to generate SQL.
    assert "Table `my_dataset.t`: id (INT64)" in vertex_client.prompts[0]
    assert "SQL" not in vertex_client.prompts[0]


def test_describe_schema_falls_back_to_raw_schema_if_explanation_fails() -> None:
    class _RaisingVertexClient:
        def generate_once(self, prompt: str) -> str:
            raise RuntimeError("model unavailable")

    bigquery_service = _FakeBigQueryService()
    agent = BigQueryAgent(_RaisingVertexClient(), bigquery_service, get_settings())

    reply = agent.describe_schema()

    assert reply == "Table `my_dataset.t`: id (INT64)"


def test_describe_schema_never_executes_a_query() -> None:
    vertex_client = _FakeVertexClient(["some explanation"])
    bigquery_service = _FakeBigQueryService()
    agent = BigQueryAgent(vertex_client, bigquery_service, get_settings())

    agent.describe_schema()

    assert bigquery_service.queries == []
