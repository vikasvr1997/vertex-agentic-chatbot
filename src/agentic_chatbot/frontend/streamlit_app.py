"""Streamlit frontend entry point. Run with:

    streamlit run src/agentic_chatbot/frontend/streamlit_app.py

Two pages:
- Chat (`app_pages/chat.py`) — the customer-facing conversational UI.
- Dev tools (`app_pages/dev_tools.py`) — links out to the architecture flow
  visualizer, a developer-only debugging tool mounted directly on the
  FastAPI backend (see `devtools/architecture_flow_tool.py`), not a
  Streamlit page itself.
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="Agentic Chatbot", page_icon="\U0001f916", layout="wide")

page = st.navigation(
    [
        st.Page("app_pages/chat.py", title="Chat", icon=":material/chat:"),
        st.Page("app_pages/dev_tools.py", title="Dev tools", icon=":material/build:"),
    ]
)
st.title(page.title, icon=page.icon)
page.run()
