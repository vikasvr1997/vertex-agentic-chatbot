"""Streamlit frontend. Run with:

    streamlit run src/agentic_chatbot/frontend/streamlit_app.py

Renders the backend's structured `{reply, generated_query, table, chart}`
response: an expander for the generated SQL, a dataframe for result rows,
and a Plotly chart when the backend inferred one.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from agentic_chatbot.frontend.backend_client import get_status, send_message
from agentic_chatbot.frontend.rendering import build_plotly_figure

_CURATED_MODELS = [
    "(use backend default)",
    "gemini-2.0-flash-001",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
]

st.set_page_config(page_title="Agentic Chatbot", page_icon="\U0001f916", layout="wide")


def _render_result(result: dict[str, Any]) -> None:
    st.markdown(str(result["reply"]))

    generated_query = result.get("generated_query")
    if generated_query:
        with st.expander("View generated query"):
            st.code(str(generated_query), language="sql")

    table = result.get("table")
    if table:
        st.dataframe(table, width="stretch")

    chart = result.get("chart")
    if chart:
        st.plotly_chart(build_plotly_figure(chart), width="stretch")


with st.sidebar:
    st.header("Connection")
    status = get_status()
    if status is None:
        st.error("Backend unreachable. Is `make run-api` running?")
    else:
        st.success("Connected via ADC — no API key in use")
        st.caption(f"**Project:** {status['google_cloud_project']}")
        st.caption(f"**Region:** {status['google_cloud_location']}")
        st.caption(f"**Backend mode:** {status['backend_mode']}")
        if status.get("bigquery_dataset"):
            st.caption(f"**BigQuery dataset:** {status['bigquery_dataset']}")
        else:
            st.caption("**BigQuery dataset:** not configured — chat-only mode")

    st.divider()
    st.header("Settings")
    selected_model = st.selectbox("Model (new sessions only)", _CURATED_MODELS)
    model_override = None if selected_model == _CURATED_MODELS[0] else selected_model

    if st.button("New conversation"):
        st.session_state.session_id = None
        st.session_state.messages = []
        st.rerun()

st.title("Agentic Chatbot (Vertex AI)")

if "session_id" not in st.session_state:
    st.session_state.session_id = None
if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        if msg["role"] == "assistant":
            _render_result(msg["result"])
        else:
            st.markdown(msg["content"])

prompt = st.chat_input("Ask something, e.g. 'how many rows are in <table>?'")
if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"), st.spinner("Thinking..."):
        try:
            result = send_message(
                prompt, st.session_state.session_id, model_override=model_override
            )
        except Exception as exc:  # noqa: BLE001 — surface any backend error to the user
            st.error(f"Backend error: {exc}")
        else:
            st.session_state.session_id = result["session_id"]
            _render_result(result)
            st.session_state.messages.append({"role": "assistant", "result": result})
