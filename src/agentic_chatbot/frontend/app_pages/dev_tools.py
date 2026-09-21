"""Dev tools — points at the architecture flow visualizer.

That tool is a separate Dash app mounted directly onto the FastAPI backend
(see `devtools/architecture_flow_tool.py` and `api/server.py`) rather than
a Streamlit page, since it ships as its own combined backend+frontend
module. This page just makes it discoverable from here.
"""

from __future__ import annotations

import streamlit as st

from agentic_chatbot.config import get_settings

st.caption("🛠️ Developer-only debugging tools — separate from the chat product.")

settings = get_settings()
tool_url = f"{settings.backend_url}/architecture-tool/"

st.markdown("### Architecture flow visualizer")
st.markdown(
    "An editable multi-service architecture flow diagram, a deterministic "
    "failure-trace chat, and a Postman-style request validator. It's mounted "
    "directly on the FastAPI backend (same process, same port) as its own "
    "app, not a Streamlit page — open it here:"
)
st.link_button(f"Open {tool_url}", tool_url)
st.caption(
    "If it doesn't load, make sure the backend (`make run-api`) is running — "
    "this tool is served by that same process."
)
