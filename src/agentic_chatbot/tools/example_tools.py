"""Example agent tools. Replace/extend these with real business logic.

Import this module (or add your own alongside it and import that too)
wherever tools need to be registered before an agent is built, e.g. in
``core/vertex_client.py`` or a dedicated agent-build script.
"""

from __future__ import annotations

from datetime import UTC, datetime

from agentic_chatbot.tools.registry import tool


@tool
def get_current_time() -> str:
    """Return the current UTC time in ISO-8601 format."""
    return datetime.now(UTC).isoformat()


@tool
def echo(text: str) -> str:
    """Echo the given text back, unchanged. Useful for testing tool wiring."""
    return text
