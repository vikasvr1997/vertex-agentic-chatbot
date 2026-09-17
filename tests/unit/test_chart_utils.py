from __future__ import annotations

import datetime as dt

from agentic_chatbot.services.chart_utils import infer_chart


def test_infer_chart_returns_none_for_empty_rows() -> None:
    assert infer_chart([]) is None


def test_infer_chart_returns_none_for_single_scalar_value() -> None:
    # A single row, single numeric column (e.g. "how many drivers?") has
    # nothing to compare against — no categorical/date axis, and only one
    # numeric column so no scatter pairing either.
    assert infer_chart([{"count": 150}]) is None


def test_infer_chart_builds_bar_spec_for_categorical_plus_numeric() -> None:
    rows = [{"category": "a", "count": 3}, {"category": "b", "count": 5}]
    chart = infer_chart(rows)
    assert chart is not None
    assert chart["type"] == "bar"
    assert chart["x"] == ["a", "b"]
    assert chart["y"] == [3, 5]
    assert chart["x_label"] == "category"
    assert chart["y_label"] == "count"


def test_infer_chart_builds_line_spec_for_date_plus_numeric() -> None:
    rows = [
        {"day": dt.date(2026, 1, 2), "revenue": 200},
        {"day": dt.date(2026, 1, 1), "revenue": 100},
    ]
    chart = infer_chart(rows)
    assert chart is not None
    assert chart["type"] == "line"
    # Sorted by the date axis regardless of input row order.
    assert chart["x"] == ["2026-01-01", "2026-01-02"]
    assert chart["y"] == [100, 200]


def test_infer_chart_builds_scatter_spec_for_two_numeric_columns() -> None:
    rows = [{"weight": 10, "price": 100}, {"weight": 20, "price": 150}]
    chart = infer_chart(rows)
    assert chart is not None
    assert chart["type"] == "scatter"
    assert chart["x"] == [10, 20]
    assert chart["y"] == [100, 150]


def test_infer_chart_builds_value_counts_bar_for_categorical_only() -> None:
    rows = [{"status": "delayed"}, {"status": "delayed"}, {"status": "on_time"}]
    chart = infer_chart(rows)
    assert chart is not None
    assert chart["type"] == "bar"
    assert chart["x"] == ["delayed", "on_time"]
    assert chart["y"] == [2, 1]
    assert chart["y_label"] == "count"


def test_infer_chart_ignores_boolean_columns_as_numeric() -> None:
    # "active" (bool) must not be treated as the numeric axis; falls back
    # to a value-counts chart of the one real categorical column.
    rows = [{"name": "a", "active": True}, {"name": "b", "active": False}]
    chart = infer_chart(rows)
    assert chart is not None
    assert chart["type"] == "bar"
    assert chart["y_label"] == "count"


def test_infer_chart_caps_points() -> None:
    rows = [{"category": str(i), "count": i} for i in range(100)]
    chart = infer_chart(rows)
    assert chart is not None
    assert len(chart["x"]) == 25
