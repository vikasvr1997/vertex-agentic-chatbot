"""Render eval results as a human-readable report."""

from __future__ import annotations

from collections.abc import Sequence

from bigquery_agent_kit.evals.runner import EvalResult


def format_eval_report(results: Sequence[EvalResult]) -> str:
    """Render eval results as a human-readable pass/fail report."""
    passed = sum(1 for result in results if result.passed)
    lines = [f"{passed}/{len(results)} passed"]
    for result in results:
        label = result.case.name or result.case.question
        status = "PASS" if result.passed else "FAIL"
        lines.append(
            f"[{status}] {label} ({result.elapsed_seconds:.2f}s, grounded={result.grounded})"
        )
        for reason in result.reasons:
            lines.append(f"    - {reason}")
    return "\n".join(lines)
