from __future__ import annotations

import csv
import io

from agentic_chatbot.frontend.rendering import (
    build_plotly_figure,
    rows_to_csv_bytes,
    rows_to_markdown_table,
)


def test_build_plotly_figure_bar() -> None:
    fig = build_plotly_figure({"type": "bar", "x": ["a", "b"], "y": [1, 2], "title": "t"})
    assert fig.data[0].type == "bar"


def test_build_plotly_figure_line() -> None:
    fig = build_plotly_figure({"type": "line", "x": ["1", "2"], "y": [1, 2]})
    assert fig.data[0].type == "scatter"
    assert fig.data[0].mode == "lines+markers"


def test_build_plotly_figure_scatter() -> None:
    fig = build_plotly_figure({"type": "scatter", "x": [1, 2], "y": [3, 4]})
    assert fig.data[0].type == "scatter"
    assert fig.data[0].mode == "markers"


def test_rows_to_csv_bytes_includes_headers() -> None:
    rows = [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
    csv_bytes = rows_to_csv_bytes(rows)

    reader = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8")))
    assert reader.fieldnames == ["id", "name"]
    assert list(reader) == [{"id": "1", "name": "a"}, {"id": "2", "name": "b"}]


def test_rows_to_csv_bytes_empty_rows() -> None:
    assert rows_to_csv_bytes([]) == b""


def test_rows_to_csv_bytes_does_not_truncate() -> None:
    rows = [{"n": i} for i in range(100)]
    csv_bytes = rows_to_csv_bytes(rows)
    reader = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8")))
    assert len(list(reader)) == 100


def test_rows_to_markdown_table_empty() -> None:
    assert rows_to_markdown_table([]) == "_No rows returned._"


def test_rows_to_markdown_table_truncates_with_note() -> None:
    rows = [{"n": i} for i in range(30)]
    table = rows_to_markdown_table(rows, max_rows=5)
    assert "Showing 5 of 30 rows" in table
