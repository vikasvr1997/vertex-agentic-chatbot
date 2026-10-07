"""Wires ADK's own BigQueryToolset to this kit's config.

Prefers Google's actively maintained BigQuery integration over a
hand-rolled one. Its ``WriteMode.BLOCKED`` guardrail runs a real BigQuery
dry run and checks the server-determined statement type — strictly more
robust than lexically scanning SQL text for forbidden keywords, since
BigQuery's own parser handles comments, string literals, and multi-statement
scripts correctly by construction. Every tool function is also wrapped to
run safely under ``asyncio`` (a sync function runs in a thread, via ADK's
own wrapping), which matters once many concurrent users hit the same agent.
"""

from __future__ import annotations

import re
import warnings
from typing import Any

from google.adk.integrations.bigquery.bigquery_toolset import BigQueryToolset
from google.adk.integrations.bigquery.config import BigQueryToolConfig, WriteMode

from bigquery_agent_kit.config import BigQueryAgentConfig

# A small, focused default: table discovery, column introspection, and a
# guarded query. Enough for most question-answering agents, and a smaller
# tool surface keeps a small/cheap model from picking the wrong one.
DEFAULT_TOOL_NAMES: tuple[str, ...] = ("list_table_ids", "get_table_info", "execute_sql")

# Everything else BigQueryToolset ships: project-level dataset discovery,
# job introspection, BigQuery ML forecasting/anomaly-detection/contribution-
# analysis, natural-language data insights, and (with the `google-adk[gcp]`
# extra) Dataplex catalog search. These involve more multi-step tool
# orchestration and model-generated SQL than the default set — optional,
# and best used with a stronger model than a "lite"/mini-class one; see
# `build_bigquery_toolset`'s warning below. Pass `bigquery_tools` on the
# config to opt into any of these.
ADVANCED_TOOL_NAMES: tuple[str, ...] = (
    "list_dataset_ids",
    "get_dataset_info",
    "get_job_info",
    "forecast",
    "analyze_contribution",
    "detect_anomalies",
    "ask_data_insights",
    "search_catalog",
)
ALL_TOOL_NAMES: tuple[str, ...] = DEFAULT_TOOL_NAMES + ADVANCED_TOOL_NAMES

# Heuristic only (name fragments of known smaller/cheaper model families) —
# there's no reliable way to ask a model string how capable it is. Used
# solely for an advisory warning, never to block anything. Matched as whole
# tokens (word-boundaried) so e.g. "mini" doesn't false-positive on
# "gemini" — it's a fragment of that name, not a size marker in it.
_WEAK_MODEL_NAME_PATTERN = re.compile(r"\b(?:lite|mini|nano|8b|small|haiku)\b", re.IGNORECASE)


def _looks_like_a_small_model(model: Any) -> bool:
    if not isinstance(model, str):
        return False
    return bool(_WEAK_MODEL_NAME_PATTERN.search(model))


def build_bigquery_toolset(config: BigQueryAgentConfig) -> BigQueryToolset:
    """Build a read-only BigQueryToolset scoped to one project/dataset.

    Credentials are intentionally left unconfigured (``credentials_config=None``):
    the underlying BigQuery client then falls back to Application Default
    Credentials, matching the rest of this package, which never reads or
    stores credentials itself.

    ``location`` is deliberately left unset on the tool config: BigQuery
    resolves each query's region from the dataset referenced, which may
    differ from ``config.google_cloud_location`` (the Vertex AI region for
    the model). Pinning this would 404 on any dataset outside that region —
    the same bug this kit's own BigQuery client fix already avoids.
    """
    tool_config = BigQueryToolConfig(
        write_mode=WriteMode.BLOCKED,
        maximum_bytes_billed=config.max_bytes_billed,
        max_query_result_rows=config.max_rows,
        default_project_id=config.google_cloud_project,
        compute_project_id=config.google_cloud_project,
        default_dataset_id=config.bigquery_default_dataset or None,
        application_name="bigquery-agent-kit",
    )
    tool_names = (
        list(config.bigquery_tools)
        if config.bigquery_tools is not None
        else list(DEFAULT_TOOL_NAMES)
    )
    advanced_selected = [name for name in tool_names if name in ADVANCED_TOOL_NAMES]
    if advanced_selected and _looks_like_a_small_model(config.model):
        warnings.warn(
            f"bigquery_tools includes advanced tool(s) {advanced_selected} with model "
            f"{config.model!r}, which looks like a smaller/cheaper model by name. These "
            "tools (BQML forecasting/anomaly-detection/contribution-analysis, "
            "natural-language insights, catalog search, dataset/job metadata) involve "
            "more multi-step tool orchestration and model-generated SQL than the "
            "default 3-tool set; a small model is more likely to call them incorrectly "
            "or misreport their results. Consider a stronger model (e.g. "
            "'gemini-2.5-flash' or 'gemini-2.5-pro') when using them.",
            UserWarning,
            stacklevel=2,
        )
    return BigQueryToolset(
        credentials_config=None,
        bigquery_tool_config=tool_config,
        tool_filter=tool_names,
    )
