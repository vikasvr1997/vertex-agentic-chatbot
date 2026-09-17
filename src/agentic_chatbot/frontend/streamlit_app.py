"""Streamlit frontend. Run with:

streamlit run src/agentic_chatbot/frontend/streamlit_app.py
"""

from __future__ import annotations

import streamlit as st

from agentic_chatbot.frontend.backend_client import send_message

st.set_page_config(page_title="Agentic Chatbot", page_icon="\U0001f916")
st.title("Agentic Chatbot (Vertex AI)")

if "session_id" not in st.session_state:
    st.session_state.session_id = None
if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

prompt = st.chat_input("Ask something...")
if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"), st.spinner("Thinking..."):
        try:
            result = send_message(prompt, st.session_state.session_id)
        except Exception as exc:  # noqa: BLE001 — surface any backend error to the user
            st.error(f"Backend error: {exc}")
        else:
            st.session_state.session_id = result["session_id"]
            reply = str(result["reply"])
            st.markdown(reply)
            st.session_state.messages.append({"role": "assistant", "content": reply})
