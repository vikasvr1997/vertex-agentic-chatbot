"""Registry of callable tools the deployed Vertex AI agent can invoke.

These are plain Python functions with type-annotated signatures and a
docstring — the same shape Vertex AI Agent Engine / function-calling
expects. Register a new tool with ``@tool`` and it becomes discoverable via
``list_tools()`` for wiring into your agent's tool config at deploy time.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

_TOOL_REGISTRY: dict[str, Callable[..., Any]] = {}


def tool(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator that registers a function as an agent-callable tool."""
    if not func.__doc__:
        raise ValueError(f"Tool '{func.__name__}' must have a docstring describing its purpose.")
    _TOOL_REGISTRY[func.__name__] = func
    return func


def list_tools() -> dict[str, Callable[..., Any]]:
    return dict(_TOOL_REGISTRY)


def get_tool(name: str) -> Callable[..., Any]:
    try:
        return _TOOL_REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f"Unknown tool '{name}'. Registered tools: {list(_TOOL_REGISTRY)}") from exc
