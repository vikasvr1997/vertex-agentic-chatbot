from __future__ import annotations

from typing import Any

import pytest
from bigquery_agent_kit.evals import EvalCase, format_eval_report, load_eval_cases, run_evals
from bigquery_agent_kit.evals.judge import JudgeVerdict


class _FakeWorkflow:
    """Stands in for BigQueryAgentWorkflow so scoring is testable without a real model."""

    def __init__(self, outcomes: dict[str, dict[str, Any]]) -> None:
        self._outcomes = outcomes
        self.questions_asked: list[str] = []

    def run(
        self, question: str, *, user_id: str = "default-user", session_id: str | None = None
    ) -> dict[str, Any]:
        self.questions_asked.append(question)
        return self._outcomes[question]


def test_load_eval_cases_from_txt_parses_question_and_optional_reference(tmp_path) -> None:
    case_file = tmp_path / "cases.txt"
    case_file.write_text(
        "# a comment\n\nHow many drivers are there? | 150\nList the tables\n",
        encoding="utf-8",
    )

    cases = load_eval_cases(case_file)

    assert cases == [
        EvalCase(question="How many drivers are there?", expected_answer="150"),
        EvalCase(question="List the tables"),
    ]


def test_load_eval_cases_from_jsonl_parses_structured_assertions(tmp_path) -> None:
    case_file = tmp_path / "cases.jsonl"
    case_file.write_text(
        '{"question": "How many drivers?", "expect_sql_contains": ["drivers"]}\n'
        '{"question": "List tables", "expect_grounded": false}\n',
        encoding="utf-8",
    )

    cases = load_eval_cases(case_file)

    assert cases[0].question == "How many drivers?"
    assert cases[0].expect_sql_contains == ("drivers",)
    assert cases[1].expect_grounded is False


def test_run_evals_fails_an_ungrounded_answer() -> None:
    case = EvalCase(question="How many drivers?")
    workflow = _FakeWorkflow(
        {
            "How many drivers?": {
                "reply": "I think there are about 150.",
                "grounded": False,
                "tool_calls": [],
                "generated_sql": None,
            }
        }
    )

    results = run_evals(workflow, [case])

    assert len(results) == 1
    assert results[0].passed is False
    assert "describe_dataset_schema" in results[0].reasons[0]


def test_run_evals_checks_sql_and_table_assertions() -> None:
    case = EvalCase(
        question="How many drivers?",
        expect_sql_contains=("COUNT",),
        expect_tables=("drivers",),
    )
    workflow = _FakeWorkflow(
        {
            "How many drivers?": {
                "reply": "There are 150 drivers.",
                "grounded": True,
                "tool_calls": ["describe_dataset_schema", "run_readonly_query"],
                "generated_sql": "SELECT COUNT(*) FROM drivers",
            }
        }
    )

    results = run_evals(workflow, [case])

    assert results[0].passed is True
    assert results[0].generated_sql == "SELECT COUNT(*) FROM drivers"


def test_run_evals_exact_match_fallback_without_judge_model() -> None:
    case = EvalCase(question="How many drivers?", expected_answer="150")
    workflow = _FakeWorkflow(
        {
            "How many drivers?": {
                "reply": "There are 200 drivers.",
                "grounded": True,
                "tool_calls": ["describe_dataset_schema"],
                "generated_sql": None,
            }
        }
    )

    results = run_evals(workflow, [case])

    assert results[0].passed is False
    assert "reference answer" in results[0].reasons[0]


def test_run_evals_uses_judge_model_when_provided(monkeypatch: pytest.MonkeyPatch) -> None:
    case = EvalCase(question="How many drivers?", expected_answer="150")
    workflow = _FakeWorkflow(
        {
            "How many drivers?": {
                "reply": "There are approximately 150 drivers on staff.",
                "grounded": True,
                "tool_calls": ["describe_dataset_schema"],
                "generated_sql": None,
            }
        }
    )

    def fake_judge_correctness(
        question: str, expected_answer: str, actual_answer: str, *, judge_model: Any
    ) -> JudgeVerdict:
        assert judge_model == "fake/judge-model"
        return JudgeVerdict(correct=True, rationale="Numbers match.")

    monkeypatch.setattr("bigquery_agent_kit.evals.runner.judge_correctness", fake_judge_correctness)

    results = run_evals(workflow, [case], judge_model="fake/judge-model")

    assert results[0].passed is True
    assert results[0].judge_rationale == "Numbers match."


def test_format_eval_report_summarizes_pass_fail_counts() -> None:
    case = EvalCase(question="How many drivers?")
    workflow = _FakeWorkflow(
        {
            "How many drivers?": {
                "reply": "150",
                "grounded": True,
                "tool_calls": ["describe_dataset_schema"],
                "generated_sql": None,
            }
        }
    )
    results = run_evals(workflow, [case])

    report = format_eval_report(results)

    assert "1/1 passed" in report
    assert "[PASS]" in report
