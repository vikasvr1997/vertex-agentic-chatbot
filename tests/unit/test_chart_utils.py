from __future__ import annotations

from agentic_chatbot.services.chart_utils import infer_chart


def test_infer_chart_returns_none_for_empty_rows() -> None:
    assert infer_chart([]) is None


def test_infer_chart_returns_none_without_numeric_column() -> None:
    rows = [{"name": "a"}, {"name": "b"}]
    assert infer_chart(rows) is None


def test_infer_chart_builds_bar_spec() -> None:
    rows = [{"category": "a", "count": 3}, {"category": "b", "count": 5}]
    chart = infer_chart(rows)
    assert chart is not None
    assert chart["type"] == "bar"
    assert chart["x"] == ["a", "b"]
    assert chart["y"] == [3, 5]
    assert chart["x_label"] == "category"
    assert chart["y_label"] == "count"


def test_infer_chart_ignores_boolean_columns_as_numeric() -> None:
    rows = [{"name": "a", "active": True}]
    assert infer_chart(rows) is None


def test_infer_chart_caps_points() -> None:
    rows = [{"category": str(i), "count": i} for i in range(100)]
    chart = infer_chart(rows)
    assert chart is not None
    assert len(chart["x"]) == 25
