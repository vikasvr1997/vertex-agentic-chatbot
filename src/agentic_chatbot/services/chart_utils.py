"""Heuristic chart-spec inference from tabular query results.

Produces the ``{"type", "x", "y", "title", ...}`` shape consumed by both
frontends' `frontend/rendering.py`. This is intentionally simple — a real
system might let the LLM choose the chart type — but a first
categorical-column-vs-first-numeric-column bar chart covers most
aggregate/"group by" query results well enough for a default.
"""

from __future__ import annotations

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
    categorical_cols = [key for key in sample if key not in numeric_cols]

    if not numeric_cols or not categorical_cols:
        return None

    x_col, y_col = categorical_cols[0], numeric_cols[0]
    limited = rows[:_MAX_POINTS]

    return {
        "type": "bar",
        "x": [str(row.get(x_col)) for row in limited],
        "y": [row.get(y_col) for row in limited],
        "title": f"{y_col} by {x_col}",
        "x_label": x_col,
        "y_label": y_col,
    }
