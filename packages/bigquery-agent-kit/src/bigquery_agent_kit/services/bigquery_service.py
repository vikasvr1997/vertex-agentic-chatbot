"""Schema-aware BigQuery client with a SELECT/WITH-only guardrail."""

from __future__ import annotations

from typing import Any

from google.cloud import bigquery

from bigquery_agent_kit.clients.bigquery_client import build_bigquery_client
from bigquery_agent_kit.config import BigQueryAgentConfig
from bigquery_agent_kit.exceptions import QueryTooExpensiveError, ReadOnlyQueryError
from bigquery_agent_kit.guardrails.sql_guardrail import validate_identifier, validate_read_only


class BigQueryService:
    """Schema discovery and guarded read-only query execution for one dataset."""

    def __init__(
        self,
        config: BigQueryAgentConfig,
        client: Any | None = None,
    ) -> None:
        self.config = config
        self._client = client or build_bigquery_client(config.google_cloud_project)
        self._schema_cache: dict[str, str] = {}
        self._graph_schema_cache: dict[str, str] = {}

    def describe_dataset(self, dataset: str | None = None) -> str:
        """Return a compact, live schema description for the configured dataset."""
        dataset_name = dataset or self.config.bigquery_default_dataset
        validate_identifier(dataset_name, kind="dataset")
        if dataset_name in self._schema_cache:
            return self._schema_cache[dataset_name]

        query = f"""
            SELECT table_name, column_name, data_type
            FROM `{self.config.google_cloud_project}.{dataset_name}.INFORMATION_SCHEMA.COLUMNS`
            ORDER BY table_name, ordinal_position
        """
        job = self._client.query(query)
        rows = list(job.result(timeout=self.config.query_timeout_seconds))

        tables: dict[str, list[str]] = {}
        for row in rows:
            tables.setdefault(row["table_name"], []).append(
                f"{row['column_name']} ({row['data_type']})"
            )
        schema = (
            "\n".join(
                f"Table `{dataset_name}.{table}`: {', '.join(columns)}"
                for table, columns in tables.items()
            )
            or f"No tables found in dataset '{dataset_name}'."
        )
        self._schema_cache[dataset_name] = schema
        return schema

    def describe_graphs(self, dataset: str | None = None) -> str:
        """Return property-graph DDL for the dataset when graphs are defined."""
        dataset_name = dataset or self.config.bigquery_default_dataset
        validate_identifier(dataset_name, kind="dataset")
        if dataset_name in self._graph_schema_cache:
            return self._graph_schema_cache[dataset_name]
        query = f"""
            SELECT ddl
            FROM `{self.config.google_cloud_project}.{dataset_name}.INFORMATION_SCHEMA.PROPERTY_GRAPHS`
        """
        try:
            job = self._client.query(query)
            rows = list(job.result(timeout=self.config.query_timeout_seconds))
        except Exception:
            rows = []
        graph_ddl = "\n\n".join(row["ddl"] for row in rows)
        if not graph_ddl:
            graph_ddl = f"No property graphs found in dataset '{dataset_name}'."
        self._graph_schema_cache[dataset_name] = graph_ddl
        return graph_ddl

    def run_query(self, sql: str) -> list[dict[str, object]]:
        """Execute one bounded read-only query and return rows as dictionaries.

        Two independent checks run before anything is billed: the lexical
        :func:`validate_read_only` (cheap, no network call, and the first
        line of defense), then a real BigQuery dry run that (a) checks the
        server-determined statement type — the authoritative check, immune
        to whatever a lexical scan might miss — and (b) estimates bytes
        processed against ``max_bytes_billed``, rejecting a full-table-scan
        question outright instead of actually billing for one.
        """
        validate_read_only(sql)

        dry_run_job = self._client.query(
            sql, job_config=bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
        )
        if dry_run_job.statement_type != "SELECT":
            raise ReadOnlyQueryError(
                "BigQuery classified this statement as "
                f"{dry_run_job.statement_type!r}, not SELECT; refusing to run it."
            )
        bytes_processed = dry_run_job.total_bytes_processed
        if bytes_processed is not None and bytes_processed > self.config.max_bytes_billed:
            raise QueryTooExpensiveError(
                f"This query would process {bytes_processed:,} bytes, exceeding the "
                f"configured cap of {self.config.max_bytes_billed:,} bytes. Add a "
                "WHERE filter, select fewer columns, or aggregate instead of "
                "scanning the full table."
            )

        job_config = bigquery.QueryJobConfig(
            use_query_cache=True,
            maximum_bytes_billed=self.config.max_bytes_billed,
        )
        job = self._client.query(sql, job_config=job_config)
        rows = job.result(
            max_results=self.config.max_rows,
            timeout=self.config.query_timeout_seconds,
        )
        return [dict(row.items()) for row in rows]

    def clear_schema_cache(self) -> None:
        """Invalidate cached table and graph metadata."""
        self._schema_cache.clear()
        self._graph_schema_cache.clear()
