"""Run eval cases against a live agent workflow and score the outcome."""

from __future__ import annotations

import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from bigquery_agent_kit.evals.cases import EvalCase
from bigquery_agent_kit.evals.judge import judge_correctness


class Workflow(Protocol):
    """Anything shaped like :class:`bigquery_agent_kit.BigQueryAgentWorkflow`."""

    def run(
        self, question: str, *, user_id: str = ..., session_id: str | None = ...
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class EvalResult:
    """Outcome of running one :class:`EvalCase` against a live agent workflow."""

    case: EvalCase
    passed: bool
    reasons: tuple[str, ...] = ()
    grounded: bool = False
    tool_calls: tuple[str, ...] = field(default_factory=tuple)
    generated_sql: str | None = None
    answer: str = ""
    elapsed_seconds: float = 0.0
    judge_rationale: str | None = None


def run_evals(
    workflow: Workflow,
    cases: Sequence[EvalCase],
    *,
    judge_model: Any | None = None,
) -> list[EvalResult]:
    """Run each case against a live agent workflow and score the outcome.

    ``workflow`` is typically a :class:`bigquery_agent_kit.BigQueryAgentWorkflow`,
    but anything exposing the same ``run(question, ...) -> dict`` shape works
    (a fake is enough to unit test cases without hitting a real model).
    """
    return [_run_one(workflow, case, judge_model=judge_model) for case in cases]


def _run_one(workflow: Workflow, case: EvalCase, *, judge_model: Any | None) -> EvalResult:
    start = time.perf_counter()
    outcome = workflow.run(case.question, user_id="eval", session_id=str(uuid.uuid4()))
    elapsed = time.perf_counter() - start

    grounded = bool(outcome.get("grounded", False))
    generated_sql = outcome.get("generated_sql")
    answer = outcome.get("reply", "")
    lowered_answer = answer.lower()
    lowered_sql = (generated_sql or "").lower()

    reasons: list[str] = []
    if case.expect_grounded and not grounded:
        reasons.append("Agent answered without calling describe_dataset_schema first.")
    for expected in case.expect_answer_contains:
        if expected.lower() not in lowered_answer:
            reasons.append(f"Answer did not contain expected text: {expected!r}")
    for expected in case.expect_sql_contains:
        if expected.lower() not in lowered_sql:
            reasons.append(f"Generated SQL did not contain expected text: {expected!r}")
    for table in case.expect_tables:
        if table.lower() not in lowered_sql:
            reasons.append(f"Generated SQL did not reference expected table: {table!r}")

    judge_rationale: str | None = None
    if case.expected_answer:
        if judge_model is not None:
            verdict = judge_correctness(
                case.question, case.expected_answer, answer, judge_model=judge_model
            )
            judge_rationale = verdict.rationale
            if not verdict.correct:
                reasons.append(f"Judge marked the answer incorrect: {verdict.rationale}")
        elif case.expected_answer.lower() not in lowered_answer:
            reasons.append(f"Answer did not contain reference answer: {case.expected_answer!r}")

    return EvalResult(
        case=case,
        passed=not reasons,
        reasons=tuple(reasons),
        grounded=grounded,
        tool_calls=tuple(outcome.get("tool_calls", ())),
        generated_sql=generated_sql,
        answer=answer,
        elapsed_seconds=elapsed,
        judge_rationale=judge_rationale,
    )
