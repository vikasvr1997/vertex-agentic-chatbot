from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bigquery_agent_kit.runtime.workflow import _NO_FINAL_RESPONSE, _summarize_events


@dataclass
class _FakeFunctionCall:
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class _FakePart:
    text: str | None = None
    function_call: _FakeFunctionCall | None = None


@dataclass
class _FakeContent:
    parts: list[_FakePart]


class _FakeEvent:
    def __init__(
        self,
        parts: list[_FakePart],
        *,
        final: bool,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self.content = _FakeContent(parts=parts) if parts else None
        self._final = final
        self.error_code = error_code
        self.error_message = error_message

    def is_final_response(self) -> bool:
        return self._final


def test_summarize_events_with_no_events_reports_no_final_response() -> None:
    summary = _summarize_events([])

    assert summary["reply"] == _NO_FINAL_RESPONSE
    assert summary["grounded"] is False
    assert summary["generated_sql"] is None
    assert summary["tool_calls"] == []


def test_summarize_events_marks_grounded_on_metadata_tool_call() -> None:
    events = [
        _FakeEvent(
            [_FakePart(function_call=_FakeFunctionCall(name="list_table_ids"))],
            final=False,
        ),
        _FakeEvent([_FakePart(text="There are 3 tables.")], final=True),
    ]

    summary = _summarize_events(events)

    assert summary["grounded"] is True
    assert summary["tool_calls"] == ["list_table_ids"]
    assert summary["generated_sql"] is None
    assert summary["reply"] == "There are 3 tables."


def test_summarize_events_extracts_sql_from_execute_sql_call() -> None:
    events = [
        _FakeEvent(
            [
                _FakePart(
                    function_call=_FakeFunctionCall(
                        name="execute_sql",
                        args={"project_id": "p", "query": "SELECT COUNT(*) FROM sales.orders"},
                    )
                )
            ],
            final=False,
        ),
        _FakeEvent([_FakePart(text="There are 42 orders.")], final=True),
    ]

    summary = _summarize_events(events)

    assert summary["generated_sql"] == "SELECT COUNT(*) FROM sales.orders"
    assert summary["tool_calls"] == ["execute_sql"]
    # execute_sql alone (no metadata tool) does not count as schema-grounded.
    assert summary["grounded"] is False


def test_summarize_events_ignores_non_final_text() -> None:
    events = [
        _FakeEvent([_FakePart(text="thinking...")], final=False),
        _FakeEvent([_FakePart(text="Final answer.")], final=True),
    ]

    summary = _summarize_events(events)

    assert summary["reply"] == "Final answer."


def test_summarize_events_surfaces_malformed_function_call_error() -> None:
    # Reproduces a real gemini-2.5-flash-lite failure mode: it emits a
    # Python-call-syntax "function call" ADK can't parse, and reports it as
    # a final event with no content, just an error code/message.
    events = [
        _FakeEvent(
            [],
            final=True,
            error_code="MALFORMED_FUNCTION_CALL",
            error_message="Malformed function call: call\nprint(default_api.foo())",
        ),
    ]

    summary = _summarize_events(events)

    assert summary["error"] is not None
    assert "MALFORMED_FUNCTION_CALL" in summary["error"]
    assert "MALFORMED_FUNCTION_CALL" in summary["reply"]
    assert summary["reply"] != _NO_FINAL_RESPONSE


def test_summarize_events_clears_error_once_a_real_answer_follows() -> None:
    events = [
        _FakeEvent([], final=True, error_code="MALFORMED_FUNCTION_CALL"),
        _FakeEvent([_FakePart(text="Recovered answer.")], final=True),
    ]

    summary = _summarize_events(events)

    assert summary["error"] is None
    assert summary["reply"] == "Recovered answer."
