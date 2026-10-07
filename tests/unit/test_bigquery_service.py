from __future__ import annotations

import pytest

from agentic_chatbot.config import get_settings
from agentic_chatbot.services.bigquery_service import (
    BigQueryService,
    ReadOnlyQueryError,
    validate_read_only,
)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM dataset.table",
        "  select id, name from dataset.table  ",
        "WITH t AS (SELECT 1 AS n) SELECT * FROM t",
    ],
)
def test_validate_read_only_accepts_select_and_with(sql: str) -> None:
    validate_read_only(sql)  # should not raise


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO dataset.table VALUES (1)",
        "UPDATE dataset.table SET x = 1",
        "DELETE FROM dataset.table",
        "DROP TABLE dataset.table",
        "CREATE TABLE dataset.table (x INT64)",
        "MERGE dataset.table USING ...",
    ],
)
def test_validate_read_only_rejects_write_statements(sql: str) -> None:
    with pytest.raises(ReadOnlyQueryError):
        validate_read_only(sql)


def test_validate_read_only_rejects_multiple_statements() -> None:
    with pytest.raises(ReadOnlyQueryError):
        validate_read_only("SELECT 1; DROP TABLE dataset.table")


def test_validate_read_only_rejects_empty_query() -> None:
    with pytest.raises(ReadOnlyQueryError):
        validate_read_only("   ")


def test_validate_read_only_rejects_select_smuggling_write_keyword() -> None:
    # A SELECT that references a column/table literally named e.g. "drop_rate"
    # should still pass — the guardrail matches whole words, not substrings.
    validate_read_only("SELECT drop_rate FROM dataset.table")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 'Drop' AS revenue_trend",
        'SELECT "delete" AS label',
        "-- DROP TABLE is not executed\nSELECT 1",
        "SELECT `dataset.drop_table` FROM `project.dataset.table`",
    ],
)
def test_validate_read_only_ignores_keywords_inside_quoted_text_and_comments(sql: str) -> None:
    validate_read_only(sql)


def test_validate_read_only_still_rejects_write_keyword_after_select() -> None:
    with pytest.raises(ReadOnlyQueryError):
        validate_read_only("SELECT 1 -- harmless comment\nDROP TABLE dataset.table")


class _FakeJob:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def result(self, timeout: float | None = None, max_results: int | None = None) -> list[dict]:
        return self._rows


class _FakeBigQueryClient:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.queries: list[str] = []

    def query(
        self,
        sql: str,
        job_config: object = None,
        location: str | None = None,
    ) -> _FakeJob:
        self.queries.append(sql)
        return _FakeJob(self.rows)


def test_describe_dataset_caches_schema() -> None:
    rows = [{"table_name": "t", "column_name": "id", "data_type": "INT64"}]
    client = _FakeBigQueryClient(rows)
    service = BigQueryService(get_settings(), client=client)

    service.describe_dataset("ds")
    service.describe_dataset("ds")

    assert len(client.queries) == 1


def test_clear_schema_cache_forces_refetch() -> None:
    rows = [{"table_name": "t", "column_name": "id", "data_type": "INT64"}]
    client = _FakeBigQueryClient(rows)
    service = BigQueryService(get_settings(), client=client)

    service.describe_dataset("ds")
    service.clear_schema_cache()
    service.describe_dataset("ds")

    assert len(client.queries) == 2


def test_describe_graphs_caches_ddl() -> None:
    rows = [{"ddl": "CREATE PROPERTY GRAPH `ds.g` NODE TABLES (t KEY (id))"}]
    client = _FakeBigQueryClient(rows)
    service = BigQueryService(get_settings(), client=client)

    first = service.describe_graphs("ds")
    second = service.describe_graphs("ds")

    assert first == second
    assert "CREATE PROPERTY GRAPH" in first
    assert len(client.queries) == 1


def test_describe_graphs_returns_placeholder_when_none_defined() -> None:
    client = _FakeBigQueryClient(rows=[])
    service = BigQueryService(get_settings(), client=client)

    result = service.describe_graphs("ds")

    assert "No property graphs found" in result


def test_clear_schema_cache_also_forces_graph_refetch() -> None:
    rows = [{"ddl": "CREATE PROPERTY GRAPH `ds.g` NODE TABLES (t KEY (id))"}]
    client = _FakeBigQueryClient(rows)
    service = BigQueryService(get_settings(), client=client)

    service.describe_graphs("ds")
    service.clear_schema_cache()
    service.describe_graphs("ds")

    assert len(client.queries) == 2
