"""Heuristic chart-spec inference from tabular query results.

Produces the ``{"type", "x", "y", "title", ...}`` shape consumed by both
frontends' `frontend/rendering.py`. Picks a chart type from the shape of
the result set — this is a heuristic, not model-driven, but covers the
common cases from aggregate/"group by" BigQuery results:

* date/timestamp + numeric  -> line (a trend over time)
* categorical + numeric     -> bar (a comparison across categories)
* two or more numeric cols  -> scatter (a relationship between two values)
* categorical only          -> bar of value counts (a distribution)
* a single scalar row       -> None (nothing to plot)

``infer_chart`` is used both for automatic charting on a successful query
and, when it returns ``None``, is what "yes, make a plot" falls back on
after the agent's follow-up offer (see ``agents/orchestrator.py``) — there
is deliberately only one chart-choosing function so both paths agree on
what's chartable.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter

_MAX_POINTS = 25


def infer_chart(rows: list[dict[str, object]]) -> dict[str, object] | None:
    if not rows:
        return None

    sample = rows[0]
    numeric_cols = [
        key
        for key, value in sample.items()
        if isinstance(value, int | float) and not isinstance(value, bool)
    ]
    date_cols = [
        key
        for key, value in sample.items()
        if isinstance(value, dt.date | dt.datetime) and key not in numeric_cols
    ]
    categorical_cols = [key for key in sample if key not in numeric_cols and key not in date_cols]

    if date_cols and numeric_cols:
        return _line_chart(rows, x_col=date_cols[0], y_col=numeric_cols[0])
    if categorical_cols and numeric_cols:
        return _bar_chart(rows, x_col=categorical_cols[0], y_col=numeric_cols[0])
    if len(numeric_cols) >= 2:
        return _scatter_chart(rows, x_col=numeric_cols[0], y_col=numeric_cols[1])
    if categorical_cols and not numeric_cols:
        return _value_counts_bar_chart(rows, col=categorical_cols[0])
    return None


def _line_chart(rows: list[dict[str, object]], x_col: str, y_col: str) -> dict[str, object]:
    limited = sorted(rows, key=lambda r: str(r.get(x_col)))[:_MAX_POINTS]
    return {
        "type": "line",
        "x": [str(row.get(x_col)) for row in limited],
        "y": [row.get(y_col) for row in limited],
        "title": f"{y_col} over {x_col}",
        "x_label": x_col,
        "y_label": y_col,
    }


def _bar_chart(rows: list[dict[str, object]], x_col: str, y_col: str) -> dict[str, object]:
    limited = rows[:_MAX_POINTS]
    return {
        "type": "bar",
        "x": [str(row.get(x_col)) for row in limited],
        "y": [row.get(y_col) for row in limited],
        "title": f"{y_col} by {x_col}",
        "x_label": x_col,
        "y_label": y_col,
    }


def _scatter_chart(rows: list[dict[str, object]], x_col: str, y_col: str) -> dict[str, object]:
    limited = rows[:_MAX_POINTS]
    return {
        "type": "scatter",
        "x": [row.get(x_col) for row in limited],
        "y": [row.get(y_col) for row in limited],
        "title": f"{y_col} vs {x_col}",
        "x_label": x_col,
        "y_label": y_col,
    }


def _value_counts_bar_chart(rows: list[dict[str, object]], col: str) -> dict[str, object]:
    counts = Counter(str(row.get(col)) for row in rows)
    top = counts.most_common(_MAX_POINTS)
    return {
        "type": "bar",
        "x": [label for label, _ in top],
        "y": [count for _, count in top],
        "title": f"Count by {col}",
        "x_label": col,
        "y_label": "count",
    }
