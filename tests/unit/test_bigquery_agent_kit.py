from __future__ import annotations

from typing import Any

import pytest
from bigquery_agent_kit import (
    BigQueryAgentConfig,
    BigQueryService,
    QueryTooExpensiveError,
    ReadOnlyQueryError,
    create_bigquery_agent,
    validate_read_only,
)
from google.adk.integrations.bigquery.bigquery_toolset import BigQueryToolset


class _FakeJob:
    def __init__(
        self,
        rows: list[dict[str, Any]],
        *,
        statement_type: str = "SELECT",
        total_bytes_processed: int = 0,
    ) -> None:
        self._rows = rows
        self.statement_type = statement_type
        self.total_bytes_processed = total_bytes_processed

    def result(
        self, timeout: float | None = None, max_results: int | None = None
    ) -> list[dict[str, Any]]:
        return self._rows


class _FakeBigQueryClient:
    def __init__(
        self,
        rows: list[dict[str, Any]],
        *,
        statement_type: str = "SELECT",
        total_bytes_processed: int = 0,
    ) -> None:
        self.rows = rows
        self.queries: list[str] = []
        self.statement_type = statement_type
        self.total_bytes_processed = total_bytes_processed

    def query(
        self,
        sql: str,
        job_config: object | None = None,
        location: str | None = None,
    ) -> _FakeJob:
        self.queries.append(sql)
        return _FakeJob(
            self.rows,
            statement_type=self.statement_type,
            total_bytes_processed=self.total_bytes_processed,
        )


def _config() -> BigQueryAgentConfig:
    return BigQueryAgentConfig(
        google_cloud_project="test-project",
        bigquery_default_dataset="sales",
    )


def test_standalone_service_reads_schema_and_caches_it() -> None:
    client = _FakeBigQueryClient(
        [{"table_name": "orders", "column_name": "order_id", "data_type": "STRING"}]
    )
    service = BigQueryService(_config(), client=client)

    schema = service.describe_dataset()
    service.describe_dataset()

    assert "Table `sales.orders`: order_id (STRING)" in schema
    assert len(client.queries) == 1


def test_standalone_service_executes_only_readonly_queries() -> None:
    client = _FakeBigQueryClient([{"order_id": "o-1"}])
    service = BigQueryService(_config(), client=client)

    rows = service.run_query("SELECT order_id FROM sales.orders")

    assert rows == [{"order_id": "o-1"}]
    with pytest.raises(ReadOnlyQueryError):
        service.run_query("SELECT 1; DROP TABLE sales.orders")


def test_factory_returns_adk_agent_wired_to_bigquery_toolset() -> None:
    agent = create_bigquery_agent(_config())

    assert agent.name == "bigquery_data_agent"
    assert len(agent.tools) == 1
    assert isinstance(agent.tools[0], BigQueryToolset)
    assert agent.model == "gemini-2.5-flash"


def test_readonly_guard_allows_drop_as_quoted_trend_label() -> None:
    validate_read_only("SELECT 'Drop' AS revenue_trend")


def test_run_query_rejects_statements_bigquerys_own_dry_run_says_are_not_select() -> None:
    # Lexically this looks like a harmless SELECT, but closes the class of
    # gap a lexical-only guardrail can't: trust BigQuery's own dry-run
    # classification as the authoritative check, not just a keyword scan.
    client = _FakeBigQueryClient([], statement_type="SCRIPT")
    service = BigQueryService(_config(), client=client)

    with pytest.raises(ReadOnlyQueryError):
        service.run_query("SELECT 1")


def test_run_query_rejects_a_full_table_scan_before_billing_for_it() -> None:
    config = BigQueryAgentConfig(
        google_cloud_project="test-project",
        bigquery_default_dataset="sales",
        max_bytes_billed=10_485_760,  # the configured 10 MB cap
    )
    # The dry run says this would process 50 GB — far over the cap.
    client = _FakeBigQueryClient([], total_bytes_processed=50 * 1024**3)
    service = BigQueryService(config, client=client)

    with pytest.raises(QueryTooExpensiveError):
        service.run_query("SELECT * FROM sales.huge_table")

    # Rejected before the real (billed) query ever ran: only the dry run happened.
    assert len(client.queries) == 1


def test_run_query_allows_a_query_within_the_byte_cap() -> None:
    config = BigQueryAgentConfig(
        google_cloud_project="test-project",
        bigquery_default_dataset="sales",
        max_bytes_billed=10_485_760,
    )
    client = _FakeBigQueryClient([{"n": 1}], total_bytes_processed=1_000_000)
    service = BigQueryService(config, client=client)

    rows = service.run_query("SELECT COUNT(*) AS n FROM sales.orders")

    assert rows == [{"n": 1}]
    assert len(client.queries) == 2  # dry run, then the real query


def test_factory_passes_through_plain_google_model_names() -> None:
    agent = create_bigquery_agent(
        BigQueryAgentConfig(
            google_cloud_project="test-project",
            bigquery_default_dataset="sales",
            model="projects/p/locations/us-central1/publishers/google/models/gemini-2.5-pro",
        ),
    )

    assert agent.model == "projects/p/locations/us-central1/publishers/google/models/gemini-2.5-pro"


def test_factory_raises_a_clear_error_for_other_providers_without_litellm() -> None:
    with pytest.raises(ImportError, match="litellm"):
        create_bigquery_agent(
            BigQueryAgentConfig(
                google_cloud_project="test-project",
                bigquery_default_dataset="sales",
                model="openai/gpt-4o-mini",
            ),
        )
