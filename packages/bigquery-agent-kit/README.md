# BigQuery Agent Kit

An installable, UI-independent Google ADK agent package for schema-grounded, read-only BigQuery analytics.

## Install

From this repository:

```bash
pip install ./packages/bigquery-agent-kit
```

Or install the subproject from Git:

```bash
pip install "bigquery-agent-kit @ git+https://github.com/<owner>/<repo>.git#subdirectory=packages/bigquery-agent-kit"
```

Authenticate with Application Default Credentials. The runtime identity needs `roles/bigquery.dataViewer` on the target dataset and `roles/bigquery.jobUser` on the project. The package never accepts or stores credentials itself.

## Project layout

```
src/bigquery_agent_kit/
  config.py          Pydantic settings (BigQueryAgentConfig) — validated, frozen
  exceptions.py        Error hierarchy (BigQueryAgentKitError, ReadOnlyQueryError)
  guardrails/           Standalone SQL/identifier safety checks (for callers not using the ADK agent)
  clients/               Factories for clients to external systems (BigQuery)
  services/               Business logic: schema discovery + guarded query execution
  runtime/                  Model resolution, the BigQuery toolset, the agent factory, the workflow wrapper
  evals/                     Optional golden-dataset groundedness/correctness checks
```

`runtime` is the ADK-agent path and uses ADK's own `BigQueryToolset` for every tool (see below) — it does not go through `services`/`guardrails` at all. Those two remain independently useful for callers who want a guarded query helper *without* an LLM agent — e.g. `services.BigQueryService` with your own framework, or `guardrails.validate_read_only` as a plain SQL safety check.

## Use the agent

```python
from bigquery_agent_kit import BigQueryAgentConfig, create_bigquery_agent

config = BigQueryAgentConfig(
    google_cloud_project="my-project",
    bigquery_default_dataset="sales",
)
root_agent = create_bigquery_agent(config)
```

`root_agent` is a Google ADK `Agent` whose tools come from ADK's own [`BigQueryToolset`](https://google.github.io/adk-docs/), not a hand-rolled implementation. By default it's scoped to a focused three-tool subset — `list_table_ids`, `get_table_info`, `execute_sql` — chosen so a small/cheap model isn't juggling tools it doesn't need. Writes are blocked by `WriteMode.BLOCKED`, which runs a real BigQuery dry run and checks the server-determined statement type (not a lexical keyword scan — BigQuery's own parser handles comments, string literals, and multi-statement scripts correctly by construction). Every tool call is also safe under `asyncio`: a sync tool function runs in a thread rather than blocking the event loop.

## Optional advanced tools

Pass `bigquery_tools` on the config to widen or narrow the tool set beyond the default three. `bigquery_agent_kit.ALL_TOOL_NAMES` is `DEFAULT_TOOL_NAMES` plus `ADVANCED_TOOL_NAMES`: `list_dataset_ids`, `get_dataset_info`, `get_job_info`, BigQuery ML's `forecast`/`analyze_contribution`/`detect_anomalies`, natural-language `ask_data_insights`, and (with the `google-adk[gcp]` extra) Dataplex's `search_catalog`:

```python
from bigquery_agent_kit import ALL_TOOL_NAMES, BigQueryAgentConfig

config = BigQueryAgentConfig(
    google_cloud_project="my-project",
    bigquery_default_dataset="sales",
    model="gemini-2.5-pro",  # a stronger model — see below
    bigquery_tools=("execute_sql", "ask_data_insights", "forecast"),
    # or bigquery_tools=ALL_TOOL_NAMES for the full catalog
)
```

These advanced tools involve more multi-step tool orchestration and more model-generated SQL (the BQML tools in particular accept either a table id *or* a raw SQL query as an argument) than the default three — a small/cheap model is more likely to call them incorrectly or misreport their results. If `bigquery_tools` includes any of them and `model` looks like a smaller model by name (`"lite"`, `"mini"`, `"nano"`, `"8b"`, `"small"`, `"haiku"` as a distinct name fragment — not a false hit inside `"gemini"`), `build_bigquery_toolset` emits a `UserWarning` recommending a stronger model instead of silently letting it run. It's advisory only, never a hard block.

## Run with ADK Web

```bash
export GOOGLE_GENAI_USE_ENTERPRISE=TRUE
export GOOGLE_CLOUD_PROJECT=my-project
export GOOGLE_CLOUD_LOCATION=us-central1
export BIGQUERY_DEFAULT_DATASET=sales
adk web packages/bigquery-agent-kit/examples
```

The example agent reads configuration from environment variables through `BigQueryAgentConfig.from_env()`.

## Embed in another application

`BigQueryAgentWorkflow` wraps the ADK `Runner` with in-memory (or your own) sessions. It has two entry points: an async `arun` and a sync `run` convenience wrapper over it.

**In production — a chatbot backend serving many concurrent users — use `arun`, and `await` it from your own async request handler:**

```python
from bigquery_agent_kit import BigQueryAgentConfig, BigQueryAgentWorkflow

workflow = BigQueryAgentWorkflow(
    BigQueryAgentConfig(
        google_cloud_project="my-project",
        bigquery_default_dataset="sales",
    )
)


async def handle_question(question: str, user_id: str) -> str:
    result = await workflow.arun(question, user_id=user_id)
    return result["reply"]
```

Each tool call already runs off the event loop (ADK runs sync BigQuery calls in a thread), and `arun` itself never blocks it either, so many `arun` calls genuinely run concurrently — confirmed with `asyncio.gather` against a real dataset: three questions answered in ~5s total, not three times a single question's latency.

`run` is a synchronous convenience wrapper (`asyncio.run(self.arun(...))`) for scripts, tests, and the eval CLI — the same local-only trade-off ADK documents on its own `Runner.run`. **Do not call it from code that already has an event loop running** (an async FastAPI handler, for instance) — `asyncio.run` can't start a second loop there; call `arun` directly instead.

```python
result = workflow.run("Show monthly revenue by region", user_id="analyst-1")
print(result["reply"])
```

For persistent sessions, provide an ADK-compatible `session_service` when creating the workflow.

Both methods return the same dict shape: `reply`, `tool_calls` (every tool the model invoked, in order), `grounded` (`True` once a metadata tool like `list_table_ids`/`get_table_info` was among them — the model read the live schema instead of guessing), `generated_sql` (the SQL from the last `execute_sql` call, or `None`), and `error` (set when the turn failed outright — e.g. a model emitting a malformed tool call ADK couldn't parse — instead of silently returning a generic "no response" message). Check `grounded` and `error` in production to catch an answer the model never actually grounded in your data, or a turn that failed without the caller knowing why.

## Using a non-Google model

`model` accepts a plain Google model name (`"gemini-2.5-flash"`), a Vertex AI resource name (`"projects/.../publishers/google/models/..."`), **or** a `"provider/model"` string, routed through [LiteLLM](https://docs.litellm.ai/) so any provider works via one input variable:

```python
config = BigQueryAgentConfig(
    google_cloud_project="my-project",
    bigquery_default_dataset="sales",
    model="openai/gpt-4o-mini",  # or "anthropic/claude-sonnet-4-5", "groq/llama-3.3-70b", ...
)
```

This needs the `litellm` extra and that provider's own credentials (an API key env var, etc.) — the kit never reads or stores them itself:

```bash
pip install "bigquery-agent-kit[litellm]"
```

You can also pass an already-built `google.adk.models.BaseLlm` instance as `model` for full control (custom endpoint, custom credentials).

## Security and cost controls

Layered, independent defenses — each one still holds if another has a gap:

- **Writes are blocked by BigQuery's own parser, not a keyword scan.** `create_bigquery_agent`'s `execute_sql` tool runs under `WriteMode.BLOCKED`: every query gets a real BigQuery dry run first, and the *server-determined* statement type must be `SELECT`. `BigQueryService.run_query` (the standalone, non-agent path) does the same — a dry-run statement-type check, layered on top of (not instead of) its own lexical `validate_read_only` pre-filter, so a lexical-only bypass still can't reach real execution.
- **A full-table-scan question is rejected before it's billed, not after.** Every query path sets `maximum_bytes_billed` from `BigQueryAgentConfig.max_bytes_billed` (default 200 MB), which BigQuery enforces as a true pre-flight check — a query that would process more fails with no charge. `BigQueryService.run_query` goes one step further and estimates the cost from the same dry run *before* even submitting the real query, raising `QueryTooExpensiveError` with an actionable message (add a filter, select fewer columns, aggregate) instead of a generic BigQuery 403. The agent's own instruction also tells the model to write narrow, filtered/aggregated SQL in the first place rather than relying on the cap to catch an inefficient query after the fact.
- **Identifiers are validated before they're interpolated into SQL.** This package's own introspection SQL (`describe_dataset`/`describe_graphs`) builds `` `project.dataset.table` `` by string interpolation — BigQuery has no placeholder syntax for identifiers. `BigQueryAgentConfig`'s `google_cloud_project`/`bigquery_default_dataset` fields, and any `dataset` override passed directly to `BigQueryService`, are validated against BigQuery's own (narrow) identifier character set via `guardrails.validate_identifier`, raising `InvalidIdentifierError` for anything that could break out of that interpolation (a backtick, for instance).
- **Prompt injection via retrieved data is called out explicitly.** The agent's instruction tells the model to treat every tool result — table names, column values, row contents — as data, never as an instruction to follow, even if it reads like one. BigQuery rows can contain free text an attacker influenced; this doesn't prevent the model from being misled by it, but keeps the model from treating it as a command.
- **Credentials are never read, stored, or passed through.** Every path — the toolset (`credentials_config=None`) and the standalone `BigQueryService` — relies solely on Application Default Credentials. Grant the runtime identity only `roles/bigquery.dataViewer` and `roles/bigquery.jobUser`; this package has no write path to abuse even if every other control somehow failed.

## Evaluating groundedness and correctness on your own data

`bigquery_agent_kit.evals` is an optional, dependency-free module for checking the agent against your own questions — did it ground its answer in the live schema, and is the answer actually correct — using the same golden-dataset pattern as OpenAI Evals, Vertex AI's Gen AI evaluation service, and DeepEval/LangSmith: a list of `{question, reference answer}` pairs you maintain and re-run whenever the prompt, model, or dataset changes.

Maintain cases in a plain-text file, one per line (`question | reference answer`; the reference answer is optional and just checks groundedness when omitted):

```
# evals/my_cases.txt
How many drivers are there? | 150
What tables are available in this dataset?
```

Or in `.jsonl`/`.json` for stricter checks (expected SQL/table references):

```jsonl
{"question": "How many drivers are there?", "expected_answer": "150", "expect_sql_contains": ["drivers"]}
```

`expect_grounded` (on by default) only confirms a metadata tool (`list_table_ids`/`get_table_info`) was called — it does **not** confirm a data answer actually came from a query. A model can read the schema, then still answer a count or sum from memory without calling `execute_sql` at all; `generated_sql` will be `None` when that happens. For any case whose answer is a specific fact from the data (not just "what tables exist"), add `expect_sql_contains` or `expect_tables` too — those fail unless a query with that content actually ran.

Run them from the command line against your real dataset:

```bash
python -m bigquery_agent_kit.evals evals/my_cases.txt evals/my_cases.jsonl
```

or programmatically:

```python
from bigquery_agent_kit import (
    BigQueryAgentConfig,
    BigQueryAgentWorkflow,
    load_eval_cases,
    run_evals,
    format_eval_report,
)

workflow = BigQueryAgentWorkflow(BigQueryAgentConfig.from_env())
cases = load_eval_cases("evals/my_cases.txt")
results = run_evals(workflow, cases)
print(format_eval_report(results))
```

By default, a case's `expected_answer` must appear verbatim in the answer. Pass `judge_model` to grade with an LLM-as-judge instead — the standard technique behind DeepEval/Ragas/OpenAI model-graded evals for free-text answers that exact matching is too brittle for — tolerating wording differences while still catching wrong numbers or missing facts. The judge model is resolved the same way as the agent's own model, so it can be a different provider entirely:

```python
results = run_evals(workflow, cases, judge_model="gemini-2.5-flash")
```

## API summary

- `BigQueryAgentConfig`: project, dataset, region, model (Google, LiteLLM `provider/model`, or a `BaseLlm` instance), row cap, bytes cap (`>= 10_485_760`, BigQuery's own minimum), timeout, and `bigquery_tools` (which `BigQueryToolset` tools to expose). Validates `google_cloud_project`/`bigquery_default_dataset` against BigQuery's identifier character set.
- `DEFAULT_TOOL_NAMES`, `ADVANCED_TOOL_NAMES`, `ALL_TOOL_NAMES`: the tool-name tuples `bigquery_tools` accepts.
- `build_bigquery_toolset(config)`: builds ADK's own `BigQueryToolset`, wired to the config above; warns if advanced tools are paired with a small-looking model.
- `BigQueryService`: a standalone schema-discovery + guarded read-only query helper for callers who don't want an LLM agent at all; accepts an injectable BigQuery client for tests. `run_query` dry-runs every query first to check the real statement type and estimate cost against `max_bytes_billed` before running it for real.
- `create_bigquery_agent(config)`: ADK root-agent factory, tools from `build_bigquery_toolset`.
- `BigQueryAgentWorkflow(config, session_service=None)`: `arun()` (async, production) and `run()` (sync convenience wrapper); both return `reply`, `grounded`, `tool_calls`, `generated_sql`, and `error`.
- `validate_read_only(sql)` / `validate_identifier(value, kind="project"|"dataset")`: standalone safety validators.
- `ReadOnlyQueryError`, `QueryTooExpensiveError`, `InvalidIdentifierError`: all subclass `BigQueryAgentKitError`.
- `bigquery_agent_kit.evals`: optional golden-dataset groundedness/correctness checks — `load_eval_cases`, `run_evals`, `format_eval_report`, and a `python -m bigquery_agent_kit.evals` CLI.

The package contains no chatbot UI, API server, plotting code, or frontend dependencies.
