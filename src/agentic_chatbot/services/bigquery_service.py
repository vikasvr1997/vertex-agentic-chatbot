"""Read-only BigQuery access — ADC only, no service-account key files.

``bigquery.Client()`` resolves credentials the same way as Vertex AI here:
via Application Default Credentials. The runtime identity (your user
account locally, or the attached service account in Cloud Run/GKE) must be
granted read-only IAM roles — ``roles/bigquery.dataViewer`` on the dataset
and ``roles/bigquery.jobUser`` on the project — and nothing that allows
writes. This module adds a second guardrail on top of that: it refuses to
execute anything that isn't a plain ``SELECT``/``WITH`` statement, even if
the credentials in use technically could.
"""

from __future__ import annotations

import re

from google.cloud import bigquery

from agentic_chatbot.config import Settings
from agentic_chatbot.core.auth import ensure_bigquery_access
from agentic_chatbot.logging_config import get_logger

logger = get_logger(__name__)

_FORBIDDEN_KEYWORDS = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "CREATE",
    "TRUNCATE",
    "MERGE",
    "GRANT",
    "REVOKE",
    "CALL",
    "EXEC",
    "EXECUTE",
}
_ALLOWED_LEADING_KEYWORDS = {"SELECT", "WITH"}


class ReadOnlyQueryError(ValueError):
    """Raised when a generated/submitted query fails the read-only guardrail."""


def validate_read_only(sql: str) -> None:
    stripped = sql.strip().rstrip(";").strip()
    if not stripped:
        raise ReadOnlyQueryError("Empty query.")
    if ";" in stripped:
        raise ReadOnlyQueryError("Multiple statements are not allowed.")

    first_word = stripped.split(None, 1)[0].upper()
    if first_word not in _ALLOWED_LEADING_KEYWORDS:
        raise ReadOnlyQueryError(
            f"Only SELECT/WITH queries are allowed; got a statement starting with '{first_word}'."
        )

    # Match whole identifiers (letters, digits, underscore) so a column or
    # table name like `drop_rate` isn't split into a bare "DROP" token.
    tokens = set(re.findall(r"\w+", stripped.upper()))
    blocked = tokens & _FORBIDDEN_KEYWORDS
    if blocked:
        raise ReadOnlyQueryError(
            f"Query contains forbidden keyword(s): {', '.join(sorted(blocked))}"
        )


class BigQueryService:
    """Thin wrapper around google-cloud-bigquery with a read-only guardrail."""

    def __init__(self, settings: Settings, client: bigquery.Client | None = None) -> None:
        self._settings = settings
        if client is None:
            if settings.bigquery_default_dataset:
                ensure_bigquery_access(
                    settings.google_cloud_project, settings.bigquery_default_dataset
                )
            client = bigquery.Client(project=settings.google_cloud_project)
        self._client = client
        self._schema_cache: str | None = None

    def run_query(self, sql: str) -> list[dict[str, object]]:
        validate_read_only(sql)
        job_config = bigquery.QueryJobConfig(
            use_query_cache=True,
            maximum_bytes_billed=self._settings.bigquery_max_bytes_billed,
        )
        logger.info("bigquery_query_submitted", sql=sql)
        job = self._client.query(sql, job_config=job_config)
        rows = job.result(
            max_results=self._settings.bigquery_max_rows,
            timeout=self._settings.bigquery_query_timeout_seconds,
        )
        return [dict(row.items()) for row in rows]

    def describe_dataset(self, dataset: str) -> str:
        """Return a compact `table: col (type), ...` schema summary, cached
        for the lifetime of this service instance."""
        if self._schema_cache is not None:
            return self._schema_cache

        query = f"""
            SELECT table_name, column_name, data_type
            FROM `{self._settings.google_cloud_project}.{dataset}.INFORMATION_SCHEMA.COLUMNS`
            ORDER BY table_name, ordinal_position
        """
        job = self._client.query(query)
        rows = list(job.result(timeout=self._settings.bigquery_query_timeout_seconds))

        tables: dict[str, list[str]] = {}
        for row in rows:
            tables.setdefault(row["table_name"], []).append(
                f"{row['column_name']} ({row['data_type']})"
            )

        if not tables:
            schema_text = f"No tables found in dataset '{dataset}'."
        else:
            schema_text = "\n".join(
                f"Table `{dataset}.{table}`: {', '.join(columns)}"
                for table, columns in tables.items()
            )

        self._schema_cache = schema_text
        return schema_text

    def clear_schema_cache(self) -> None:
        """Force the next `describe_dataset` call to re-fetch from
        INFORMATION_SCHEMA — used by the "Refresh schema" UI action after
        tables are added/changed."""
        self._schema_cache = None
