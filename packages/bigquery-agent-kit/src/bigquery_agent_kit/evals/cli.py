"""CLI entry point: ``python -m bigquery_agent_kit.evals cases.jsonl [more.txt ...]``."""

from __future__ import annotations

import sys
from collections.abc import Sequence

from bigquery_agent_kit.config import BigQueryAgentConfig
from bigquery_agent_kit.evals.cases import EvalCase, load_eval_cases
from bigquery_agent_kit.evals.report import format_eval_report
from bigquery_agent_kit.evals.runner import run_evals
from bigquery_agent_kit.runtime.workflow import BigQueryAgentWorkflow


def main(argv: Sequence[str] | None = None) -> int:
    paths = list(argv if argv is not None else sys.argv[1:])
    if not paths:
        print("usage: python -m bigquery_agent_kit.evals <cases-file> [...]", file=sys.stderr)
        return 2

    cases: list[EvalCase] = []
    for path in paths:
        cases.extend(load_eval_cases(path))

    workflow = BigQueryAgentWorkflow(BigQueryAgentConfig.from_env())
    results = run_evals(workflow, cases)
    print(format_eval_report(results))
    return 0 if all(result.passed for result in results) else 1
