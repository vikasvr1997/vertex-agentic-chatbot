"""ADK Runner wrapper for applications that need a simple ask/answer call.

:meth:`BigQueryAgentWorkflow.arun` is the real implementation and the one to
use in production: a chatbot backend serving many concurrent users must not
block its event loop for the seconds a model+BigQuery round trip takes.
:meth:`BigQueryAgentWorkflow.run` is a synchronous convenience wrapper over
it for scripts, tests, and the eval CLI — the same local-only trade-off ADK
documents on ``Runner.run`` itself ("only for local testing and convenience
purpose; consider using `run_async` for production usage").
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterable
from typing import Any

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from bigquery_agent_kit.config import BigQueryAgentConfig
from bigquery_agent_kit.runtime.agent import create_bigquery_agent

# Any one of these means the agent read live BigQuery metadata before
# answering, rather than guessing table/column names.
_SCHEMA_TOOL_NAMES = frozenset(
    {"list_dataset_ids", "get_dataset_info", "list_table_ids", "get_table_info"}
)
_QUERY_TOOL_NAME = "execute_sql"
_NO_FINAL_RESPONSE = "The ADK agent returned no final response."


def _summarize_events(events: Iterable[Any]) -> dict[str, Any]:
    """Reduce a run's events to an answer plus a groundedness trace.

    Pulled out of :meth:`BigQueryAgentWorkflow.arun` as a pure function so
    the extraction logic is unit-testable against plain fake events,
    without a real model or a real ``Runner``.
    """
    answer = _NO_FINAL_RESPONSE
    tool_calls: list[str] = []
    generated_sql: str | None = None
    error: str | None = None
    for event in events:
        error_code = getattr(event, "error_code", None)
        if error_code:
            # E.g. MALFORMED_FUNCTION_CALL — a model occasionally emits a
            # tool call ADK can't parse. ADK marks this event final with no
            # content, so without this check the caller would silently see
            # the generic "no final response" fallback below instead of
            # knowing the turn actually failed.
            error = f"{error_code}: {getattr(event, 'error_message', '') or ''}".strip(": ")
            continue
        content = getattr(event, "content", None) or getattr(event, "message", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            function_call = getattr(part, "function_call", None)
            if function_call is None:
                continue
            tool_calls.append(function_call.name)
            if function_call.name == _QUERY_TOOL_NAME:
                query = (function_call.args or {}).get("query")
                if isinstance(query, str) and query:
                    generated_sql = query
        if not event.is_final_response():
            continue
        text = "".join(part.text or "" for part in parts if getattr(part, "text", None))
        if text.strip():
            answer = text.strip()
            error = None
    if error and answer == _NO_FINAL_RESPONSE:
        answer = f"The agent encountered an error and could not answer: {error}"
    return {
        "reply": answer,
        "tool_calls": tool_calls,
        "grounded": any(name in _SCHEMA_TOOL_NAMES for name in tool_calls),
        "generated_sql": generated_sql,
        "error": error,
    }


class BigQueryAgentWorkflow:
    """Reusable ADK workflow with pluggable session storage."""

    def __init__(
        self,
        config: BigQueryAgentConfig,
        session_service: Any | None = None,
        app_name: str = "bigquery_agent_kit",
    ) -> None:
        self.config = config
        self.app_name = app_name
        self.session_service = session_service or InMemorySessionService()
        self.agent = create_bigquery_agent(config)
        self.runner = Runner(
            agent=self.agent,
            app_name=app_name,
            session_service=self.session_service,
        )

    async def arun(
        self,
        question: str,
        *,
        user_id: str = "default-user",
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Run a user question and return its response, session ID, and a groundedness trace.

        The returned dict always has ``session_id`` and ``reply``. It also
        carries ``tool_calls`` (names of every tool the model invoked, in
        order), ``grounded`` (``True`` once a metadata tool like
        ``list_table_ids``/``get_table_info`` was among them — i.e. the
        model read the live schema instead of guessing), and
        ``generated_sql`` (the SQL from the last ``execute_sql`` call, or
        ``None`` if it never queried). Use these to spot an ungrounded or
        hallucinated answer in production, or feed them to
        :mod:`bigquery_agent_kit.evals` for batch checks.
        """
        resolved_session_id = session_id or str(uuid.uuid4())
        session = await self.session_service.get_session(
            app_name=self.app_name,
            user_id=user_id,
            session_id=resolved_session_id,
        )
        if session is None:
            await self.session_service.create_session(
                app_name=self.app_name,
                user_id=user_id,
                session_id=resolved_session_id,
            )

        message = types.Content(role="user", parts=[types.Part(text=question)])
        events = [
            event
            async for event in self.runner.run_async(
                user_id=user_id,
                session_id=resolved_session_id,
                new_message=message,
            )
        ]
        return {"session_id": resolved_session_id, **_summarize_events(events)}

    def run(
        self,
        question: str,
        *,
        user_id: str = "default-user",
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Synchronous convenience wrapper over :meth:`arun`.

        For local scripts, tests, and the eval CLI only. Calling this from
        code that already has an event loop running (an async FastAPI
        handler, for instance) raises, because ``asyncio.run`` cannot start
        a second loop — call :meth:`arun` directly there instead.
        """
        return asyncio.run(self.arun(question, user_id=user_id, session_id=session_id))
