from __future__ import annotations

import warnings

import pytest
from bigquery_agent_kit.config import BigQueryAgentConfig
from bigquery_agent_kit.runtime.toolset import (
    ADVANCED_TOOL_NAMES,
    ALL_TOOL_NAMES,
    DEFAULT_TOOL_NAMES,
    build_bigquery_toolset,
)
from google.adk.integrations.bigquery.config import WriteMode


def _config(**overrides: object) -> BigQueryAgentConfig:
    defaults: dict[str, object] = {
        "google_cloud_project": "test-project",
        "bigquery_default_dataset": "sales",
    }
    defaults.update(overrides)
    return BigQueryAgentConfig(**defaults)  # type: ignore[arg-type]


def test_build_bigquery_toolset_always_blocks_writes() -> None:
    toolset = build_bigquery_toolset(_config())

    assert toolset._tool_settings.write_mode == WriteMode.BLOCKED


def test_build_bigquery_toolset_maps_caps_and_defaults() -> None:
    toolset = build_bigquery_toolset(_config(max_rows=42, max_bytes_billed=50_000_000))

    settings = toolset._tool_settings
    assert settings.max_query_result_rows == 42
    assert settings.maximum_bytes_billed == 50_000_000
    assert settings.default_project_id == "test-project"
    assert settings.compute_project_id == "test-project"
    assert settings.default_dataset_id == "sales"
    # Never pinned: BigQuery resolves region from the dataset, not Vertex's region.
    assert settings.location is None


def test_build_bigquery_toolset_uses_default_tool_names() -> None:
    toolset = build_bigquery_toolset(_config())

    assert toolset.tool_filter == list(DEFAULT_TOOL_NAMES)


def test_build_bigquery_toolset_honors_explicit_tool_filter() -> None:
    toolset = build_bigquery_toolset(_config(bigquery_tools=("execute_sql",)))

    assert toolset.tool_filter == ["execute_sql"]


def test_build_bigquery_toolset_treats_empty_dataset_as_no_default() -> None:
    toolset = build_bigquery_toolset(_config(bigquery_default_dataset=""))

    assert toolset._tool_settings.default_dataset_id is None


def test_build_bigquery_toolset_uses_adc_not_explicit_credentials() -> None:
    toolset = build_bigquery_toolset(_config())

    assert toolset._credentials_config is None


def test_all_tool_names_is_default_plus_advanced() -> None:
    assert set(ALL_TOOL_NAMES) == set(DEFAULT_TOOL_NAMES) | set(ADVANCED_TOOL_NAMES)
    assert set(DEFAULT_TOOL_NAMES).isdisjoint(ADVANCED_TOOL_NAMES)


def test_build_bigquery_toolset_can_opt_into_advanced_tools() -> None:
    toolset = build_bigquery_toolset(_config(bigquery_tools=ALL_TOOL_NAMES))

    assert toolset.tool_filter == list(ALL_TOOL_NAMES)


def test_build_bigquery_toolset_warns_when_advanced_tools_used_with_a_small_model() -> None:
    with pytest.warns(UserWarning, match="forecast"):
        build_bigquery_toolset(
            _config(model="gemini-2.5-flash-lite", bigquery_tools=("execute_sql", "forecast"))
        )


def test_build_bigquery_toolset_does_not_warn_for_default_tools_with_a_small_model() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        build_bigquery_toolset(_config(model="gemini-2.5-flash-lite"))


def test_build_bigquery_toolset_does_not_warn_for_advanced_tools_with_a_strong_model() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        build_bigquery_toolset(_config(model="gemini-2.5-pro", bigquery_tools=ALL_TOOL_NAMES))
