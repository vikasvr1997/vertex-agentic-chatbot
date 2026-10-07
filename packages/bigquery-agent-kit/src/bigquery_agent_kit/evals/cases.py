"""Load golden eval cases from a user-editable file."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EvalCase:
    """One data question to check the agent against, and what a correct run looks like."""

    question: str
    name: str | None = None
    expect_grounded: bool = True
    expected_answer: str | None = None
    expect_answer_contains: tuple[str, ...] = ()
    expect_sql_contains: tuple[str, ...] = ()
    expect_tables: tuple[str, ...] = ()


def load_eval_cases(path: str | Path) -> list[EvalCase]:
    """Load eval cases from a file you can hand-edit without touching code.

    Two formats, chosen by extension:

    * ``.json``/``.jsonl`` — a JSON array of objects, or one JSON object per
      line, with keys matching :class:`EvalCase` fields. Use this when you
      need SQL/table assertions alongside the question.
    * anything else (conventionally ``.txt``) — one case per line:
      ``question | reference answer``. The ``| reference answer`` part is
      optional; omit it to check groundedness only. Blank lines and lines
      starting with ``#`` are ignored.
    """
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    if file_path.suffix in {".json", ".jsonl"}:
        return [_case_from_mapping(raw) for raw in _parse_json_cases(text)]
    return [_case_from_line(line) for line in text.splitlines() if _is_case_line(line)]


def _is_case_line(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def _case_from_line(line: str) -> EvalCase:
    question, _, reference = line.partition("|")
    reference = reference.strip()
    return EvalCase(question=question.strip(), expected_answer=reference or None)


def _parse_json_cases(text: str) -> list[dict[str, Any]]:
    stripped = text.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        return json.loads(stripped)
    return [json.loads(line) for line in stripped.splitlines() if line.strip()]


def _case_from_mapping(raw: dict[str, Any]) -> EvalCase:
    return EvalCase(
        question=raw["question"],
        name=raw.get("name"),
        expect_grounded=raw.get("expect_grounded", True),
        expected_answer=raw.get("expected_answer"),
        expect_answer_contains=tuple(raw.get("expect_answer_contains", ())),
        expect_sql_contains=tuple(raw.get("expect_sql_contains", ())),
        expect_tables=tuple(raw.get("expect_tables", ())),
    )
