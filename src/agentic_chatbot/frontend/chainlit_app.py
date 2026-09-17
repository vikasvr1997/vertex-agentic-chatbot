"""Chainlit frontend. Run with:

chainlit run src/agentic_chatbot/frontend/chainlit_app.py
"""

from __future__ import annotations

import chainlit as cl

from agentic_chatbot.frontend.backend_client import send_message


@cl.on_message
async def on_message(message: cl.Message) -> None:
    session_id = cl.user_session.get("session_id")

    try:
        result = send_message(message.content, session_id)
    except Exception as exc:  # noqa: BLE001 — surface any backend error to the user
        await cl.Message(content=f"Backend error: {exc}").send()
        return

    cl.user_session.set("session_id", result["session_id"])
    await cl.Message(content=str(result["reply"])).send()
