"""Natural-language -> read-only BigQuery SQL agent.

Generates SQL with a stateless Gemini call grounded in the target
dataset's schema (introspected via ``INFORMATION_SCHEMA``), then executes
it through ``BigQueryService``, which enforces the SELECT/WITH-only
guardrail regardless of what the model produced.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentic_chatbot.config import Settings
from agentic_chatbot.core.vertex_client import VertexAgentClient
from agentic_chatbot.logging_config import get_logger
from agentic_chatbot.services.bigquery_service import BigQueryService, ReadOnlyQueryError

logger = get_logger(__name__)

_SQL_SYSTEM_PROMPT = """You are a BigQuery SQL generation assistant with READ-ONLY access to one dataset.

Rules:
- Output ONLY a single BigQuery Standard SQL statement, starting with SELECT or WITH.
- No prose, no markdown code fences, no trailing semicolon, no multiple statements.
- Only reference tables/columns listed in the schema below.
- If the question cannot be answered with a read-only query against this schema, output exactly: NO_QUERY

Schema:
{schema}

Question: {question}
SQL:"""

_SUMMARY_PROMPT = """You just ran a read-only BigQuery query to answer the user's question.
Write a concise, natural-language answer/summary of the results below — explain
what the numbers mean in context of the question, don't just restate the raw
table. The full result table is shown separately in the UI, so don't repeat
every row verbatim; call out the notable values.

Question: {question}
SQL used: {sql}
Result rows ({shown} of {total} shown):
{rows_text}

Answer:"""

_SUMMARY_MAX_ROWS = 20


@dataclass
class BigQueryAgentResult:
    reply: str
    generated_query: str | None
    table: list[dict[str, object]] | None


class BigQueryAgent:
    def __init__(
        self,
        vertex_client: VertexAgentClient,
        bigquery_service: BigQueryService,
        settings: Settings,
    ) -> None:
        self._vertex_client = vertex_client
        self._bigquery_service = bigquery_service
        self._settings = settings

    def answer(self, question: str) -> BigQueryAgentResult:
        schema = self._bigquery_service.describe_dataset(self._settings.bigquery_default_dataset)
        prompt = _SQL_SYSTEM_PROMPT.format(schema=schema, question=question)

        sql = self._vertex_client.generate_once(prompt).strip()
        sql = sql.removeprefix("```sql").removeprefix("```").removesuffix("```").strip()

        if not sql or sql.upper() == "NO_QUERY":
            return BigQueryAgentResult(
                reply=(
                    "I couldn't translate that into a read-only query against the "
                    f"'{self._settings.bigquery_default_dataset}' dataset."
                ),
                generated_query=None,
                table=None,
            )

        try:
            rows = self._bigquery_service.run_query(sql)
        except ReadOnlyQueryError as exc:
            logger.warning("bigquery_guardrail_blocked", sql=sql, reason=str(exc))
            return BigQueryAgentResult(
                reply=f"Refused to run a non-read-only query: {exc}",
                generated_query=sql,
                table=None,
            )
        except Exception as exc:  # noqa: BLE001 — surface any BigQuery error to the user
            logger.error("bigquery_query_failed", sql=sql, error=str(exc))
            return BigQueryAgentResult(
                reply=f"The query failed: {exc}",
                generated_query=sql,
                table=None,
            )

        if not rows:
            return BigQueryAgentResult(
                reply="The query returned no rows.", generated_query=sql, table=rows
            )

        reply = self._summarize(question, sql, rows)
        return BigQueryAgentResult(reply=reply, generated_query=sql, table=rows)

    def _summarize(self, question: str, sql: str, rows: list[dict[str, object]]) -> str:
        preview = rows[:_SUMMARY_MAX_ROWS]
        rows_text = "\n".join(str(row) for row in preview)
        prompt = _SUMMARY_PROMPT.format(
            question=question,
            sql=sql,
            shown=len(preview),
            total=len(rows),
            rows_text=rows_text,
        )
        try:
            return self._vertex_client.generate_once(prompt).strip()
        except Exception as exc:  # noqa: BLE001 — fall back to a plain count rather than failing the turn
            logger.warning("bigquery_summary_failed", error=str(exc))
            return f"Found {len(rows)} row(s)."
