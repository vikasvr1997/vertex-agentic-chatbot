"""Rendering helpers shared by the Streamlit and Chainlit frontends.

Both UIs receive the same structured `{reply, generated_query, table,
chart}` shape from the backend and need to turn `chart` into a figure and
`table` into a markdown fallback (Chainlit has no native dataframe
element; Streamlit uses `st.dataframe` directly instead of the markdown
table, but the same `chart` spec drives a Plotly figure in both).
"""

from __future__ import annotations

from typing import Any

import plotly.graph_objects as go

_DARK_TEMPLATE = "plotly_dark"


def build_plotly_figure(chart: dict[str, Any]) -> go.Figure:
    chart_type = chart.get("type", "bar")
    x, y = chart.get("x", []), chart.get("y", [])

    fig = go.Figure()
    if chart_type == "line":
        fig.add_trace(go.Scatter(x=x, y=y, mode="lines+markers"))
    else:
        fig.add_trace(go.Bar(x=x, y=y))

    fig.update_layout(
        title=chart.get("title", ""),
        xaxis_title=chart.get("x_label", ""),
        yaxis_title=chart.get("y_label", ""),
        template=_DARK_TEMPLATE,
        margin={"l": 10, "r": 10, "t": 40, "b": 10},
        height=360,
    )
    return fig


def rows_to_markdown_table(rows: list[dict[str, Any]], max_rows: int = 25) -> str:
    if not rows:
        return "_No rows returned._"

    columns = list(rows[0].keys())
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    body_lines = [
        "| " + " | ".join(str(row.get(col, "")) for col in columns) + " |"
        for row in rows[:max_rows]
    ]

    table = "\n".join([header, separator, *body_lines])
    if len(rows) > max_rows:
        table += f"\n\n_Showing {max_rows} of {len(rows)} rows._"
    return table
