"""Optional golden-dataset evals for the BigQuery agent kit.

Nothing outside this subpackage imports it, and it adds no dependency
beyond the kit's own (``google-adk``, ``google-cloud-bigquery``). Use it to
check, against your own dataset and your own questions:

* **Groundedness** — did the agent call ``describe_dataset_schema`` (read the
  live schema) before answering, instead of guessing table/column names?
* **Correctness** — does the answer match a reference answer you supply?

The case file format follows the golden-dataset pattern used by OpenAI
Evals, Vertex AI's Gen AI evaluation service, and tools like DeepEval/
LangSmith: a list of ``{question, reference answer}`` pairs you maintain
yourself (see :mod:`bigquery_agent_kit.evals.cases`) and re-run whenever the
prompt, model, or dataset changes.

Correctness grading has two modes (see :mod:`bigquery_agent_kit.evals.judge`):

* No ``judge_model`` passed to :func:`run_evals` — a case with
  ``expected_answer`` passes only if that text appears verbatim in the
  answer (fast, deterministic, exact-match).
* A ``judge_model`` passed — an LLM-as-judge call (the standard technique
  behind DeepEval/Ragas/OpenAI model-graded evals) compares the answer to
  ``expected_answer`` and tolerates wording differences. The judge model is
  resolved through the same multi-provider mechanism as the agent itself,
  so it can be Gemini, or any LiteLLM ``provider/model`` string.

Run a case file from the command line with
``python -m bigquery_agent_kit.evals cases.jsonl``.
"""

from bigquery_agent_kit.evals.cases import EvalCase, load_eval_cases
from bigquery_agent_kit.evals.cli import main
from bigquery_agent_kit.evals.judge import JudgeVerdict, judge_correctness
from bigquery_agent_kit.evals.report import format_eval_report
from bigquery_agent_kit.evals.runner import EvalResult, Workflow, run_evals

__all__ = [
    "EvalCase",
    "EvalResult",
    "JudgeVerdict",
    "Workflow",
    "format_eval_report",
    "judge_correctness",
    "load_eval_cases",
    "main",
    "run_evals",
]
