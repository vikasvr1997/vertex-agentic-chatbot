from __future__ import annotations

import pytest

from agentic_chatbot.services.bigquery_service import ReadOnlyQueryError, validate_read_only


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
