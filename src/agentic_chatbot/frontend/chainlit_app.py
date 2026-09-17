"""Chainlit frontend. Run with:

    chainlit run src/agentic_chatbot/frontend/chainlit_app.py

Uses Chainlit's `ChatSettings` as the sidebar-equivalent config panel, and
renders the backend's structured `{reply, generated_query, table, chart}`
response as side-panel elements: the generated SQL as a code block, the
result rows as a markdown table, and the chart as an inline Plotly figure —
using the same `rendering.py` chart builder the Streamlit app uses.
"""

from __future__ import annotations

import chainlit as cl
from chainlit.input_widget import InputWidget, Select

from agentic_chatbot.frontend.backend_client import get_status, send_message
from agentic_chatbot.frontend.rendering import build_plotly_figure, rows_to_markdown_table

_CURATED_MODELS = [
    "(use backend default)",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.5-flash-lite",
]


@cl.on_chat_start
async def on_chat_start() -> None:
    widgets: list[InputWidget] = [
        Select(
            id="model", label="Model (new sessions only)", values=_CURATED_MODELS, initial_index=0
        )
    ]
    await cl.ChatSettings(widgets).send()

    status = get_status()
    if status is None:
        await cl.Message(content="⚠️ Backend unreachable. Is `make run-api` running?").send()
        return

    dataset_line = (
        f"BigQuery dataset: `{status['bigquery_dataset']}`"
        if status.get("bigquery_dataset")
        else "BigQuery dataset: not configured — chat-only mode"
    )
    await cl.Message(
        content=(
            "Connected via ADC — no API key in use.\n\n"
            f"- Project: `{status['google_cloud_project']}`\n"
            f"- Region: `{status['google_cloud_location']}`\n"
            f"- Backend mode: `{status['backend_mode']}`\n"
            f"- {dataset_line}"
        )
    ).send()


@cl.on_settings_update
async def on_settings_update(settings: dict[str, object]) -> None:
    cl.user_session.set("model", settings.get("model"))


@cl.on_message
async def on_message(message: cl.Message) -> None:
    session_id = cl.user_session.get("session_id")
    selected_model = cl.user_session.get("model")
    model_override = None if selected_model in (None, _CURATED_MODELS[0]) else str(selected_model)

    try:
        result = send_message(message.content, session_id, model_override=model_override)
    except Exception as exc:  # noqa: BLE001 — surface any backend error to the user
        await cl.Message(content=f"Backend error: {exc}").send()
        return

    cl.user_session.set("session_id", result["session_id"])

    elements: list[cl.Element] = []
    generated_query = result.get("generated_query")
    if generated_query:
        elements.append(
            cl.Text(name="Generated SQL", content=f"```sql\n{generated_query}\n```", display="side")
        )

    table = result.get("table")
    if table:
        elements.append(
            cl.Text(name="Result table", content=rows_to_markdown_table(table), display="side")
        )

    chart = result.get("chart")
    if chart:
        elements.append(
            cl.Plotly(name="chart", figure=build_plotly_figure(chart), display="inline")
        )

    await cl.Message(content=str(result["reply"]), elements=elements).send()
