from __future__ import annotations

import pytest
from bigquery_agent_kit.config import BigQueryAgentConfig
from pydantic import ValidationError


def test_config_rejects_unknown_fields_and_bad_values() -> None:
    with pytest.raises(ValidationError):
        BigQueryAgentConfig(google_cloud_project="p", bigquery_default_dataset="d", unknown_field=1)
    with pytest.raises(ValidationError):
        BigQueryAgentConfig(google_cloud_project="p", bigquery_default_dataset="d", max_rows=-1)


def test_config_rejects_bytes_cap_below_bigquerys_own_minimum() -> None:
    with pytest.raises(ValidationError):
        BigQueryAgentConfig(
            google_cloud_project="p",
            bigquery_default_dataset="d",
            max_bytes_billed=1_000,
        )


def test_config_is_frozen() -> None:
    config = BigQueryAgentConfig(google_cloud_project="p", bigquery_default_dataset="d")

    with pytest.raises(ValidationError):
        config.max_rows = 5  # type: ignore[misc]


def test_config_bigquery_tools_defaults_to_none() -> None:
    config = BigQueryAgentConfig(google_cloud_project="p", bigquery_default_dataset="d")

    assert config.bigquery_tools is None


def test_from_env_parses_comma_separated_tool_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
    monkeypatch.setenv("BIGQUERY_DEFAULT_DATASET", "d")
    monkeypatch.setenv("BIGQUERY_TOOLS", "execute_sql, ask_data_insights")

    config = BigQueryAgentConfig.from_env()

    assert config.bigquery_tools == ("execute_sql", "ask_data_insights")


def test_config_rejects_a_project_id_that_could_break_out_of_sql() -> None:
    with pytest.raises(ValidationError):
        BigQueryAgentConfig(
            google_cloud_project="p`; DROP TABLE x --",
            bigquery_default_dataset="d",
        )


def test_config_rejects_a_dataset_id_that_could_break_out_of_sql() -> None:
    with pytest.raises(ValidationError):
        BigQueryAgentConfig(
            google_cloud_project="p",
            bigquery_default_dataset="d`.x`; DROP TABLE y --",
        )


def test_config_allows_empty_dataset_as_the_disabled_sentinel() -> None:
    config = BigQueryAgentConfig(google_cloud_project="p", bigquery_default_dataset="")

    assert config.bigquery_default_dataset == ""


def test_config_allows_legacy_domain_scoped_project_id() -> None:
    config = BigQueryAgentConfig(
        google_cloud_project="example.com:my-project", bigquery_default_dataset="d"
    )

    assert config.google_cloud_project == "example.com:my-project"
