"""Main chat page.

Renders the backend's structured `{reply, generated_query, table, chart}`
response: an expander for the generated SQL, a dataframe + CSV export for
result rows, and a Plotly chart when one was inferred (or the user opted
into the follow-up "want a chart?" offer).

The sidebar holds a Claude-style conversation history list (switch/delete)
with connection status, model choice, and schema refresh tucked behind a
bottom "Settings" popover rather than always-expanded panels.
"""

from __future__ import annotations

import uuid
from typing import Any

import streamlit as st

from agentic_chatbot.frontend.backend_client import get_status, refresh_schema, send_message
from agentic_chatbot.frontend.rendering import build_plotly_figure, rows_to_csv_bytes

_CURATED_MODELS = [
    "(use backend default)",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.5-flash-lite",
]
_TITLE_MAX_LEN = 40


def _new_conversation() -> dict[str, Any]:
    return {"title": "New conversation", "backend_session_id": None, "messages": []}


def _init_state() -> None:
    if "conversations" not in st.session_state:
        conv_id = str(uuid.uuid4())
        st.session_state.conversations = {conv_id: _new_conversation()}
        st.session_state.active_conv_id = conv_id


def _active_conversation() -> dict[str, Any]:
    return st.session_state.conversations[st.session_state.active_conv_id]


def _create_conversation() -> None:
    conv_id = str(uuid.uuid4())
    st.session_state.conversations[conv_id] = _new_conversation()
    st.session_state.active_conv_id = conv_id


def _delete_conversation(conv_id: str) -> None:
    st.session_state.conversations.pop(conv_id, None)
    if not st.session_state.conversations:
        _create_conversation()
    elif st.session_state.active_conv_id == conv_id:
        st.session_state.active_conv_id = next(iter(st.session_state.conversations))


def _render_result(result: dict[str, Any], key_suffix: str) -> None:
    st.markdown(str(result["reply"]))

    generated_query = result.get("generated_query")
    if generated_query:
        with st.expander("View generated query"):
            st.code(str(generated_query), language="sql")

    table = result.get("table")
    if table:
        st.dataframe(table, width="stretch")
        st.download_button(
            "\U0001f4e5 Export to CSV",
            data=rows_to_csv_bytes(table),
            file_name="query_results.csv",
            mime="text/csv",
            key=f"csv_export_{key_suffix}",
        )

    chart = result.get("chart")
    if chart:
        st.plotly_chart(build_plotly_figure(chart), width="stretch")


_init_state()

with st.sidebar:
    st.markdown("### Conversations")
    if st.button("➕ New conversation", width="stretch"):
        _create_conversation()
        st.rerun()

    for conv_id, conv in reversed(list(st.session_state.conversations.items())):
        is_active = conv_id == st.session_state.active_conv_id
        col_title, col_delete = st.columns([5, 1])
        with col_title:
            label = ("➤ " if is_active else "") + conv["title"]
            if st.button(label, key=f"switch_{conv_id}", width="stretch"):
                st.session_state.active_conv_id = conv_id
                st.rerun()
        with col_delete:
            if st.button("\U0001f5d1", key=f"delete_{conv_id}", help="Delete conversation"):
                _delete_conversation(conv_id)
                st.rerun()

    st.divider()

    with st.popover("⚙️ Settings", width="stretch"):
        st.markdown("**Connection**")
        status = get_status()
        if status is None:
            st.error("Backend unreachable. Is `make run-api` running?")
        else:
            st.success("Connected via ADC — no API key in use")
            st.caption(f"Project: {status['google_cloud_project']}")
            st.caption(f"Region: {status['google_cloud_location']}")
            st.caption(f"Backend mode: {status['backend_mode']}")
            if status.get("bigquery_dataset"):
                st.caption(f"BigQuery dataset: {status['bigquery_dataset']}")
            else:
                st.caption("BigQuery dataset: not configured — chat-only mode")

        st.divider()
        st.markdown("**Model** _(new sessions only)_")
        selected_model = st.selectbox("Model", _CURATED_MODELS, label_visibility="collapsed")
        model_override = None if selected_model == _CURATED_MODELS[0] else selected_model

        st.divider()
        if st.button("\U0001f504 Refresh schema", width="stretch"):
            if refresh_schema():
                st.success("Schema cache cleared.")
            else:
                st.error("Couldn't reach the backend to refresh the schema.")

conv = _active_conversation()

for i, msg in enumerate(conv["messages"]):
    with st.chat_message(msg["role"]):
        if msg["role"] == "assistant":
            _render_result(msg["result"], key_suffix=f"{st.session_state.active_conv_id}_{i}")
        else:
            st.markdown(msg["content"])

prompt = st.chat_input("Ask something, e.g. 'how many rows are in <table>?'")
if prompt:
    conv["messages"].append({"role": "user", "content": prompt})
    if conv["title"] == "New conversation":
        conv["title"] = prompt[:_TITLE_MAX_LEN] + ("…" if len(prompt) > _TITLE_MAX_LEN else "")
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"), st.spinner("Thinking..."):
        try:
            result = send_message(prompt, conv["backend_session_id"], model_override=model_override)
        except Exception as exc:  # noqa: BLE001 — surface any backend error to the user
            st.error(f"Backend error: {exc}")
        else:
            conv["backend_session_id"] = result["session_id"]
            conv["messages"].append({"role": "assistant", "result": result})
            _render_result(
                result, key_suffix=f"{st.session_state.active_conv_id}_{len(conv['messages']) - 1}"
            )
