from __future__ import annotations

import pytest

from agentic_chatbot.tools import example_tools  # noqa: F401 — registers example tools
from agentic_chatbot.tools.registry import get_tool, list_tools, tool


def test_example_tools_are_registered() -> None:
    tools = list_tools()
    assert "get_current_time" in tools
    assert "echo" in tools


def test_echo_tool_returns_input() -> None:
    echo = get_tool("echo")
    assert echo("hello") == "hello"


def test_get_tool_raises_for_unknown_name() -> None:
    with pytest.raises(KeyError):
        get_tool("does_not_exist")


def test_tool_decorator_requires_docstring() -> None:
    with pytest.raises(ValueError):

        @tool
        def undocumented() -> None:
            pass
