"""Reusable Google ADK workflow and read-only tools for BigQuery.

Project layout:

- ``config``       Pydantic settings (``BigQueryAgentConfig``).
- ``exceptions``    The error hierarchy.
- ``guardrails``    Standalone SQL/identifier safety checks (for callers not using the ADK agent).
- ``clients``       Factories for clients to external systems (BigQuery).
- ``services``      Business logic: schema discovery + guarded query execution.
- ``runtime``       Model resolution, the BigQuery toolset, the agent factory, the workflow wrapper.
- ``evals``         Optional golden-dataset groundedness/correctness checks.
"""

from bigquery_agent_kit.config import BigQueryAgentConfig
from bigquery_agent_kit.evals import (
    EvalCase,
    EvalResult,
    format_eval_report,
    load_eval_cases,
    run_evals,
)
from bigquery_agent_kit.exceptions import (
    BigQueryAgentKitError,
    InvalidIdentifierError,
    QueryTooExpensiveError,
    ReadOnlyQueryError,
)
from bigquery_agent_kit.guardrails import validate_identifier, validate_read_only
from bigquery_agent_kit.runtime import (
    ADVANCED_TOOL_NAMES,
    ALL_TOOL_NAMES,
    DEFAULT_TOOL_NAMES,
    BigQueryAgentWorkflow,
    build_bigquery_toolset,
    create_bigquery_agent,
)
from bigquery_agent_kit.services import BigQueryService

__all__ = [
    "ADVANCED_TOOL_NAMES",
    "ALL_TOOL_NAMES",
    "DEFAULT_TOOL_NAMES",
    "BigQueryAgentConfig",
    "BigQueryAgentKitError",
    "BigQueryAgentWorkflow",
    "BigQueryService",
    "EvalCase",
    "EvalResult",
    "InvalidIdentifierError",
    "QueryTooExpensiveError",
    "ReadOnlyQueryError",
    "build_bigquery_toolset",
    "create_bigquery_agent",
    "format_eval_report",
    "load_eval_cases",
    "run_evals",
    "validate_identifier",
    "validate_read_only",
]
