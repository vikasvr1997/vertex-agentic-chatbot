"""Natural-language -> read-only BigQuery SQL/GQL agent.

Two generation paths, both grounded in live BigQuery metadata and both
executed through ``BigQueryService``, which enforces the SELECT/WITH-only
guardrail regardless of what the model produced:

* ``answer()`` — flat SQL, grounded in ``INFORMATION_SCHEMA.COLUMNS``. Good
  for filters/aggregates over one table or a simple join.
* ``answer_graph()`` — GQL via ``GRAPH_TABLE(...)``, grounded in the raw
  property-graph DDL from ``INFORMATION_SCHEMA.PROPERTY_GRAPHS``. Good for
  relationship/path/multi-hop questions a flat join can't express cleanly
  (e.g. "how is this incident connected to the customer?", "what's
  N hops from this facility?").

The orchestrator decides which path a question needs; this agent just
executes whichever one it's asked for.
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass

from google.api_core.exceptions import BadRequest

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
- Use BigQuery Standard SQL functions; do not use SQLite functions such as strftime.
- For multi-step calculations, aggregate in a CTE before applying window functions.
- Do not nest window functions. Calculate prior-period values from the aggregated rows.
- Include every requested result field in the final SELECT, including distinct counts.
- Return percentage changes as percentages (multiply the ratio by 100) and use SAFE_DIVIDE.
- If the question cannot be answered with a read-only query against this schema, output exactly: NO_QUERY

Schema:
{schema}

Schema-specific guidance:
{schema_hints}

Question: {question}
SQL:"""

_GQL_SYSTEM_PROMPT = """You are a BigQuery GQL generation assistant with READ-ONLY access to one \
or more property graphs.

The graph DDL below models entities as NODE TABLES and relationships as EDGE TABLES (each edge \
has a SOURCE and a DESTINATION). Use this path when the question is about a RELATIONSHIP, PATH, \
or CHAIN across connected entities — not something a single flat table filter could answer.

Rules:
- Output ONLY a single BigQuery Standard SQL statement of the form:
  SELECT ... FROM GRAPH_TABLE(graph_name MATCH ... RETURN ...) [WHERE ...] [ORDER BY ...] [LIMIT ...]
- The statement must start with SELECT or WITH.
- No prose, no markdown code fences, no trailing semicolon, no multiple statements.
- Only reference node/edge tables, labels, and properties that appear in the graph DDL below.
- SYNTAX: edge-pattern arrows have NO whitespace anywhere in them: (a)-[:REL]->(b) and \
(a)<-[:REL]-(b) are correct; "(a) <- [:REL] - (b)" is a syntax error.
- SYNTAX: any label that is not a plain identifier — i.e. it contains a dot, hyphen, or any \
character other than letters/digits/underscore, which is the case for every node label in the \
graphs below — MUST be wrapped in backticks in the MATCH clause, e.g. \
(c:`{{project}}.{{dataset}}.customer_data`), never (c:{{project}}.{{dataset}}.customer_data) \
unquoted.
- SCOPING (this is the single most common mistake — read carefully): graph pattern variables \
declared in MATCH (like `c` in `(c:...)`, or `f` in `(f:...)`) exist ONLY inside the \
GRAPH_TABLE(...) parentheses — in the MATCH and WHERE clauses that sit inside GRAPH_TABLE(...), \
and inside RETURN itself. They do NOT exist anywhere outside the closing parenthesis of \
GRAPH_TABLE(...): not in the outer SELECT list, not in an outer WHERE, not in GROUP BY, not in \
ORDER BY. This applies even when you write "AS" — writing "f.facility_name AS facility_name" in \
the OUTER SELECT is JUST AS WRONG as writing bare "f.facility_name" there, because `f` itself is \
still unrecognized outside GRAPH_TABLE(...). The dotted `variable.property` form and any alias \
for it belong ONLY inside RETURN (e.g. "RETURN f.facility_name AS facility_name" inside \
GRAPH_TABLE(...)). Everywhere outside GRAPH_TABLE(...) — the outer SELECT included — reference \
ONLY the bare column name RETURN produced (e.g. "SELECT facility_name", never \
"SELECT f.facility_name" and never "SELECT f.facility_name AS facility_name"). If a property \
isn't referenced anywhere outside GRAPH_TABLE(...), it doesn't need a RETURN alias at all, but \
if you do reference it outside, it MUST already have been aliased inside RETURN under that exact \
bare name.
- For "how are X and Y connected" / "N hops away" questions, use a variable-length path quantifier \
(e.g. ->{{1,3}}) rather than guessing a fixed number of hops.
- If the question cannot be answered with a read-only graph query against these graphs, output \
exactly: NO_QUERY

WRONG (pattern variable `f` used outside GRAPH_TABLE(...), even with AS — this fails with \
"Unrecognized name: f"):
SELECT f.facility_name AS facility_name
FROM GRAPH_TABLE(
  `project.dataset.local_graph`
  MATCH (f:`project.dataset.facilities`)<-[:some_edge_alias]-(x:`project.dataset.some_table`)
  RETURN DISTINCT f.facility_name
)

RIGHT (alias assigned inside RETURN; outer SELECT uses only the bare resulting column name):
SELECT DISTINCT facility_name
FROM GRAPH_TABLE(
  `project.dataset.local_graph`
  MATCH (f:`project.dataset.facilities`)<-[:some_edge_alias]-(x:`project.dataset.some_table`)
  RETURN f.facility_name AS facility_name
)

Worked example of correct scoping with a WHERE filter and aggregation (aggregating a RETURNed \
column by its bare name, not by pattern variable):
SELECT customer_name, COUNT(*) AS incident_count
FROM GRAPH_TABLE(
  `project.dataset.local_graph`
  MATCH (c:`project.dataset.customer_data`)<-[:some_edge_alias]-(x:`project.dataset.some_table`)
  RETURN c.customer_name AS customer_name
)
GROUP BY customer_name

Graphs:
{graphs}

Question: {question}
GQL:"""

_SCHEMA_EXPLANATION_PROMPT = """You have read-only query access to one BigQuery dataset.
Explain to the user, in clear and friendly language, what data is available to them:
list the tables and briefly describe what each one likely represents based on its
columns. Do not invent tables, columns, or data that aren't in the schema below —
describe only what's actually there.

Schema:
{schema}

Answer:"""


@dataclass
class BigQueryAgentResult:
    reply: str
    generated_query: str | None
    table: list[dict[str, object]] | None
    query_duration_ms: int | None = None


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
        prompt = _SQL_SYSTEM_PROMPT.format(
            schema=schema,
            schema_hints=self._schema_hints(question, schema),
            question=question,
        )

        template_sql = self._freight_monthly_query(question, schema)
        if template_sql is not None:
            return self._run_query(
                template_sql,
                empty_reply="The query returned no rows.",
                grounding_prompt=prompt,
            )

        model_name = self._settings.analytics_model_name
        sql = self._generate_query(prompt, model_name)
        if sql is None and model_name != self._settings.vertex_model_name:
            model_name = self._settings.vertex_model_name
            sql = self._generate_query(prompt, model_name)
        if sql is None:
            return BigQueryAgentResult(
                reply=(
                    "I couldn't translate that into a read-only query against the "
                    f"'{self._settings.bigquery_default_dataset}' dataset."
                ),
                generated_query=None,
                table=None,
            )

        contract_issues = self._query_contract_issues(question, schema, sql)
        if contract_issues:
            repaired_sql = None
            if model_name != self._settings.vertex_model_name:
                repaired_sql = self._repair_query(
                    prompt,
                    sql,
                    "The candidate query does not satisfy the request: "
                    + "; ".join(contract_issues),
                )
            if repaired_sql is None or self._query_contract_issues(question, schema, repaired_sql):
                return BigQueryAgentResult(
                    reply=(
                        "I couldn't safely form a query with all the requested fields. "
                        "Try asking for fewer calculations at once."
                    ),
                    generated_query=repaired_sql or sql,
                    table=None,
                )
            sql = repaired_sql
            model_name = self._settings.vertex_model_name

        return self._run_query(
            sql,
            empty_reply="The query returned no rows.",
            grounding_prompt=prompt,
            allow_repair=model_name != self._settings.vertex_model_name,
        )

    def answer_graph(self, question: str) -> BigQueryAgentResult:
        """Answer a relationship/path/multi-hop question by generating and
        running a GQL query against the dataset's property graph(s)."""
        graphs = self._bigquery_service.describe_graphs(self._settings.bigquery_default_dataset)
        prompt = _GQL_SYSTEM_PROMPT.format(graphs=graphs, question=question)

        sql = self._generate_query(prompt, self._settings.vertex_model_name)
        if sql is None:
            return BigQueryAgentResult(
                reply=(
                    "I couldn't translate that into a graph query against the "
                    f"'{self._settings.bigquery_default_dataset}' property graph(s)."
                ),
                generated_query=None,
                table=None,
            )

        return self._run_query(
            sql,
            empty_reply="The graph query returned no matches.",
            grounding_prompt=prompt,
        )

    def describe_schema(self) -> str:
        """Answer a meta-question about the dataset itself (e.g. "what data
        do you have?") directly from the live schema — no SQL generation,
        no execution. Falls back to the raw schema text if the explanation
        call itself fails, so this never leaves the user with nothing."""
        schema = self._bigquery_service.describe_dataset(self._settings.bigquery_default_dataset)
        prompt = _SCHEMA_EXPLANATION_PROMPT.format(schema=schema)
        try:
            return self._vertex_client.generate_once(prompt).strip()
        except Exception as exc:  # noqa: BLE001 — fall back to the raw schema rather than failing
            logger.warning("bigquery_schema_explanation_failed", error=str(exc))
            return schema

    @staticmethod
    def _schema_hints(question: str, schema: str) -> str:
        normalized_question = question.lower()
        schema_lower = schema.lower()
        hints: list[str] = []

        asks_freight_category = re.search(
            r"\bfreight\s+(?:types?|categories|classes)\b", normalized_question
        )
        if (
            asks_freight_category
            and "primary_freight_type" in schema_lower
            and "customer_data" in schema_lower
            and "loads" in schema_lower
        ):
            hints.append(
                "For customer freight type/category, use "
                "customer_data.primary_freight_type, not loads.load_type. "
                "Join loads to customer_data on customer_id."
            )

        asks_shipment_count = any(
            phrase in normalized_question
            for phrase in ("number of shipments", "shipment count", "number of loads", "load count")
        )
        if asks_shipment_count and "load_id" in schema_lower:
            hints.append(
                "Count shipments as COUNT(DISTINCT loads.load_id), and include that count "
                "in the final output."
            )

        if not hints:
            return "No additional schema-specific guidance. Follow the schema exactly."
        return "\n".join(f"- {hint}" for hint in hints)

    def _freight_monthly_query(self, question: str, schema: str) -> str | None:
        normalized_question = " ".join(question.lower().split())
        if (
            "freight" not in normalized_question
            or "month" not in normalized_question
            or not any(term in normalized_question for term in ("top", "most", "highest"))
        ):
            return None

        schema_lower = schema.lower()
        loads_line = next(
            (line.lower() for line in schema.splitlines() if ".loads`:" in line.lower()),
            "",
        )
        customers_line = next(
            (line.lower() for line in schema.splitlines() if ".customer_data`:" in line.lower()),
            "",
        )
        required_load_fields = ("load_id (", "customer_id (", "load_date (date)", "revenue (")
        if (
            not loads_line
            or not customers_line
            or any(field not in loads_line for field in required_load_fields)
            or "customer_id (" not in customers_line
            or "primary_freight_type (" not in customers_line
            or "primary_freight_type" not in schema_lower
        ):
            return None

        rank = 3
        count_match = re.search(
            r"\b(?:top|which)\s+(\d+|one|two|three|four|five)\b", normalized_question
        )
        if count_match:
            rank_values = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
            rank = min(
                10,
                int(count_match.group(1))
                if count_match.group(1).isdigit()
                else rank_values[count_match.group(1)],
            )

        return f"""WITH monthly AS (
  SELECT DATE_TRUNC(l.load_date, MONTH) AS month,
         c.primary_freight_type,
         SUM(l.revenue) AS total_revenue,
         COUNT(DISTINCT l.load_id) AS number_of_shipments
  FROM `{self._settings.google_cloud_project}.{self._settings.bigquery_default_dataset}.loads` AS l
  JOIN `{self._settings.google_cloud_project}.{self._settings.bigquery_default_dataset}.customer_data` AS c
    ON l.customer_id = c.customer_id
  WHERE l.load_date IS NOT NULL AND l.revenue IS NOT NULL
  GROUP BY month, c.primary_freight_type
), with_previous_month AS (
  SELECT month, primary_freight_type, total_revenue, number_of_shipments,
         LAG(total_revenue) OVER (PARTITION BY primary_freight_type ORDER BY month) AS previous_month_revenue
  FROM monthly
), ranked AS (
  SELECT month, primary_freight_type, total_revenue, number_of_shipments,
         SAFE_DIVIDE(total_revenue - previous_month_revenue, previous_month_revenue) * 100 AS percentage_change,
         ROW_NUMBER() OVER (PARTITION BY month ORDER BY total_revenue DESC) AS revenue_rank
  FROM with_previous_month
)
SELECT month, primary_freight_type, total_revenue, number_of_shipments, percentage_change, revenue_rank
FROM ranked
WHERE revenue_rank <= {rank}
ORDER BY month, total_revenue DESC"""

    @staticmethod
    def _query_contract_issues(question: str, schema: str, sql: str) -> list[str]:
        normalized_question = question.lower()
        normalized_sql = sql.lower()
        schema_lower = schema.lower()
        issues: list[str] = []

        asks_freight_category = re.search(
            r"\bfreight\s+(?:types?|categories|classes)\b", normalized_question
        )
        if asks_freight_category and "primary_freight_type" in schema_lower:
            if "primary_freight_type" not in normalized_sql:
                issues.append(
                    "use customer_data.primary_freight_type for the requested freight category"
                )
            if (
                "customer_data" in schema_lower
                and "loads" in schema_lower
                and ("customer_data" not in normalized_sql or "customer_id" not in normalized_sql)
            ):
                issues.append("join loads to customer_data using their customer_id relationship")

        asks_shipment_count = any(
            phrase in normalized_question
            for phrase in ("number of shipments", "shipment count", "number of loads", "load count")
        )
        if (
            asks_shipment_count
            and "load_id" in schema_lower
            and not re.search(
                r"\bcount\s*\(\s*distinct\s+(?:\w+\s*\.\s*)?load_id\b",
                normalized_sql,
            )
        ):
            issues.append("count distinct load_id values for the shipment count")

        asks_percentage_change = (
            "percent" in normalized_question or "percentage" in normalized_question
        ) and "change" in normalized_question
        if asks_percentage_change:
            if not re.search(r"\blag\s*\(", normalized_sql):
                issues.append("calculate the previous-period value with LAG")
            if "safe_divide" not in normalized_sql or not re.search(
                r"(?:\*\s*100(?:\.0)?|100(?:\.0)?\s*\*)", normalized_sql
            ):
                issues.append("return percentage change using SAFE_DIVIDE multiplied by 100")

        asks_top_three = bool(
            re.search(r"\btop\s+(?:three|3)\b", normalized_question)
            or re.search(
                r"\bthree\s+freight\s+(?:types?|categories|classes)\b", normalized_question
            )
        )
        if asks_top_three:
            if not re.search(r"\b(?:rank|dense_rank|row_number)\s*\(", normalized_sql):
                issues.append("rank the requested categories within each month")
            if not re.search(r"(?:<=\s*3|=\s*3)", normalized_sql):
                issues.append("filter the final results to the top three")

        final_select_at = normalized_sql.rfind("select")
        final_select = normalized_sql[final_select_at:]
        projection_match = re.search(r"\bselect\b(.*?)\bfrom\b", final_select, re.DOTALL)
        projection = projection_match.group(1) if projection_match else ""
        if (
            asks_freight_category
            and "primary_freight_type" in schema_lower
            and "primary_freight_type" not in projection
        ):
            issues.append("include the requested freight category in the final output")
        if (
            asks_shipment_count
            and "load_id" in schema_lower
            and not re.search(r"shipment|load_count|distinct_load", projection)
        ):
            issues.append("include the distinct shipment count in the final output")
        if asks_percentage_change and not re.search(r"change|percent", projection):
            issues.append("include the percentage-change result in the final output")

        return issues

    def _generate_query(self, prompt: str, model_name: str) -> str | None:
        """Run a generation prompt and normalize the result. Returns None
        when the model declined (empty output or the NO_QUERY sentinel)."""
        generation_started_ns = time.perf_counter_ns()
        sql = self._vertex_client.generate_once(prompt, model_override=model_name).strip()
        sql = sql.removeprefix("```sql").removeprefix("```").removesuffix("```").strip()
        declined = not sql or sql.upper() == "NO_QUERY"
        logger.info(
            "bigquery_sql_generation_completed",
            model=model_name,
            duration_ms=(time.perf_counter_ns() - generation_started_ns) // 1_000_000,
            output_characters=len(sql),
            declined=declined,
        )
        if declined:
            return None
        return sql

    def _run_query(
        self,
        sql: str,
        empty_reply: str,
        grounding_prompt: str,
        allow_repair: bool = True,
        prior_query_duration_ms: int = 0,
    ) -> BigQueryAgentResult:
        try:
            query_started_ns = time.perf_counter_ns()
            rows = self._bigquery_service.run_query(sql)
            query_duration_ms = (
                prior_query_duration_ms + (time.perf_counter_ns() - query_started_ns) // 1_000_000
            )
        except ReadOnlyQueryError as exc:
            logger.warning("bigquery_guardrail_blocked", sql=sql, reason=str(exc))
            return BigQueryAgentResult(
                reply=f"Refused to run a non-read-only query: {exc}",
                generated_query=sql,
                table=None,
            )
        except BadRequest as exc:
            prior_query_duration_ms += (time.perf_counter_ns() - query_started_ns) // 1_000_000
            repaired_sql = (
                self._repair_query(grounding_prompt, sql, str(exc)) if allow_repair else None
            )
            if repaired_sql and repaired_sql != sql:
                logger.info("bigquery_query_repair_retry")
                return self._run_query(
                    repaired_sql,
                    empty_reply=empty_reply,
                    grounding_prompt=grounding_prompt,
                    allow_repair=False,
                    prior_query_duration_ms=prior_query_duration_ms,
                )
            logger.error("bigquery_query_failed", sql=sql, error=str(exc))
            return BigQueryAgentResult(
                reply=f"The query failed: {exc}",
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
                reply=empty_reply,
                generated_query=sql,
                table=rows,
                query_duration_ms=query_duration_ms,
            )

        reply = self._summarize_rows(rows)
        return BigQueryAgentResult(
            reply=reply,
            generated_query=sql,
            table=rows,
            query_duration_ms=query_duration_ms,
        )

    @staticmethod
    def _summarize_rows(rows: list[dict[str, object]]) -> str:
        reply = f"The query returned {len(rows)} row(s). Results are shown below."
        if not rows:
            return reply

        change_column = next(
            (
                column
                for column in rows[0]
                if "change" in column.lower()
                and any(token in column.lower() for token in ("percent", "pct"))
            ),
            None,
        )
        if change_column is None:
            return reply

        changes: list[tuple[float, dict[str, object]]] = []
        for row in rows:
            value = row.get(change_column)
            if value is None:
                continue
            try:
                change = float(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
            if math.isfinite(change):
                changes.append((change, row))

        if not changes:
            return reply

        def describe(entry: tuple[float, dict[str, object]]) -> str:
            change, row = entry
            dimensions = [
                str(row[column])
                for column in row
                if any(token in column.lower() for token in ("month", "freight_type", "category"))
                and row[column] is not None
            ]
            label = " / ".join(dimensions) or "the returned results"
            return f"{label} ({change:+.1f}%)"

        largest_increase = max(changes, key=lambda item: item[0])
        largest_decrease = min(changes, key=lambda item: item[0])
        if largest_increase[0] > 0:
            reply += f" Largest revenue increase: {describe(largest_increase)}."
        if largest_decrease[0] < 0:
            reply += f" Largest revenue decrease: {describe(largest_decrease)}."
        return reply

    def _repair_query(self, grounding_prompt: str, sql: str, error: str) -> str | None:
        repair_prompt = (
            "Repair this failed BigQuery Standard SQL query. Use only the tables and columns "
            "in the original schema, keep the user's requested calculations and output fields, "
            "and return exactly one read-only SELECT/WITH statement with no prose.\n\n"
            f"Original request and schema:\n{grounding_prompt}\n\n"
            f"Failed SQL:\n{sql}\n\nBigQuery error:\n{error}\n\nCorrected SQL:"
        )
        try:
            return self._generate_query(repair_prompt, self._settings.vertex_model_name)
        except Exception as exc:  # noqa: BLE001 — preserve the original query error
            logger.warning("bigquery_query_repair_failed", error=str(exc))
            return None
