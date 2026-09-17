# Agentic Chatbot (Vertex AI)

Enterprise conversational agentic chatbot backed by Google Cloud Vertex AI,
with both Streamlit and Chainlit frontends sharing one authenticated FastAPI
backend.

See [TODO.md](TODO.md) for the known gaps between "the agent works" and
"this is production-ready" (session persistence, per-user auth, rate
limiting, evaluation harness, etc.).

## Key design decisions

- **ADC only, no API keys.** All Vertex AI connectivity goes through
  Application Default Credentials (`gcloud auth application-default login`
  locally, an attached service account on Cloud Run/GKE). See
  `src/agentic_chatbot/core/auth.py`. This is enforced by design — there is
  no code path that accepts a Vertex AI / Gemini API key.
- **Two Vertex AI backends, one interface.** Before you deploy a Vertex AI
  Agent Engine, the backend talks directly to a Gemini model
  (`GeminiModelBackend`). Once you set `VERTEX_AGENT_ENGINE_ID`, it switches
  to querying your deployed Agent Engine (`ReasoningEngineBackend`) with no
  code changes — see `.agents/skills/deploy-to-vertex-agent-engine/SKILL.md`.
- **One backend, two frontends.** Streamlit and Chainlit are thin clients
  that both call the same authenticated FastAPI service
  (`src/agentic_chatbot/api/`), so agent logic, sessions, and Vertex AI
  connectivity live in exactly one place.
- **Orchestrator routes chat vs. data queries.** Every message goes through
  `Orchestrator`, which classifies it (a stateless Gemini call) and routes
  to either the general `ChatAgent` or the read-only `BigQueryAgent`. If no
  `BIGQUERY_DEFAULT_DATASET` is configured, classification is skipped and
  everything goes to chat — no BigQuery access is attempted unless you opt
  in. See `src/agentic_chatbot/agents/orchestrator.py`.
- **BigQuery access is read-only, twice over.** The BigQuery agent generates
  SQL grounded in the dataset's live schema, but `BigQueryService` refuses
  to execute anything that isn't a bare `SELECT`/`WITH` statement — even if
  the model or a caller supplied something else — on top of the ADC
  identity itself being granted only `roles/bigquery.dataViewer` +
  `roles/bigquery.jobUser`. See `src/agentic_chatbot/services/bigquery_service.py`.

## Project layout

```
src/agentic_chatbot/
  config.py            Settings (env-driven, no API keys)
  logging_config.py    Structured (JSON) logging
  core/
    auth.py            ADC verification
    vertex_client.py   Vertex AI backends (Gemini model / Agent Engine)
    conversation.py    In-memory session tracking
  agents/
    orchestrator.py    Routes each message to chat or BigQuery
    chat_agent.py       General conversation (via vertex_client)
    bigquery_agent.py    NL -> SQL -> rows, schema-grounded
  services/
    bigquery_service.py  Read-only BigQuery client + guardrail
    chart_utils.py        Heuristic chart-spec inference from rows
  tools/               Agent-callable tools (function-calling)
  api/                 FastAPI backend (bearer-token authenticated)
    routes/chat.py       POST /chat -> orchestrator
    routes/status.py     GET /status -> non-secret config for the UI sidebar
  frontend/
    streamlit_app.py
    chainlit_app.py
    rendering.py         Shared Plotly/markdown-table helpers
tests/                 pytest unit + integration tests
openapi/openapi.yaml   Generated OpenAPI spec, audited by 42Crunch in CI
.github/workflows/     CI, SonarQube, 42Crunch, CodeQL
.agents/skills/        Runbooks for coding agents (deploy, add tools, security scans)
```

### Chat response shape

`POST /chat` returns `{session_id, reply, turn_count, generated_query, table, chart}`.
`generated_query`/`table`/`chart` are `null` for a plain chat turn, and
populated when the orchestrator routed to the BigQuery agent. Both
frontends render `generated_query` as a collapsible SQL block, `table` as
a data table, and `chart` (`{type, x, y, title, x_label, y_label}`) as a
Plotly figure.

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

gcloud auth application-default login
gcloud config set project <your-gcp-project-id>

cp .env.example .env   # set GOOGLE_CLOUD_PROJECT at minimum
```

Run all three services:

```bash
make run-api         # terminal 1 — :8080
make run-streamlit    # terminal 2 — :8501
make run-chainlit      # terminal 3 — :8000
```

Or via Docker Compose (mounts your host's ADC credentials read-only):

```bash
make docker-up
```

## Enabling the BigQuery data-query agent (optional)

Leave `BIGQUERY_DEFAULT_DATASET` empty to run chat-only. To enable NL -> SQL
querying against a dataset:

```bash
# Grant the ADC identity read-only access — never a write role.
gcloud projects add-iam-policy-binding <project-id> \
  --member="user:you@example.com" \
  --role="roles/bigquery.dataViewer"
gcloud projects add-iam-policy-binding <project-id> \
  --member="user:you@example.com" \
  --role="roles/bigquery.jobUser"
```

Then set `BIGQUERY_DEFAULT_DATASET=your_dataset` in `.env` and restart the
API. The orchestrator will start classifying messages and routing
data-shaped questions to the BigQuery agent.

### Going further: separate, A2A-connected GCP agents

The orchestrator/BigQuery agent above run in-process. If you need them (plus
a GCS data agent) as independently deployed, independently scaled Vertex AI
agents talking over the Agent2Agent (A2A) protocol — e.g. for per-agent IAM
isolation or reuse by other orchestrators — see
`docs/gcp-multi-agent-a2a-setup.md` for the full walkthrough (ADK agent
code, IAM tables, A2A wiring, Agentspace registration) and
`.agents/skills/deploy-multi-agent-a2a/SKILL.md` for the condensed runbook.

## Testing

```bash
make test          # pytest + coverage
make lint           # ruff
make typecheck      # mypy
```

Tests never require real GCP credentials — `core.vertex_client` is mocked
at the `vertexai` boundary (see `tests/unit/test_vertex_client.py`).

## CI/CD and security scanning

- **`ci.yml`** — lint, type-check, test on Python 3.11 and 3.12.
- **`sonarqube.yml`** — static analysis / code quality. Requires the
  `SONAR_TOKEN` (and `SONAR_HOST_URL` for self-hosted SonarQube; use
  `https://sonarcloud.io` for SonarCloud) repository secrets.
- **`42crunch.yml`** — audits `openapi/openapi.yaml` for API security
  issues. Requires a 42Crunch account and the `CRUNCH42_API_TOKEN` repo
  secret. Regenerate the spec after API changes: `make openapi`.
- **`codeql.yml`** — GitHub's static security analysis, no extra setup.

None of these secrets are included in this repo — add them under
**Settings → Secrets and variables → Actions** once you provision the
corresponding SonarQube/SonarCloud project and 42Crunch account.

## Deploying your own agent to Vertex AI

See `.agents/skills/deploy-to-vertex-agent-engine/SKILL.md` for the full
runbook. In short: build your agent with the Vertex AI Agent Engine SDK,
register tools from `tools/registry.py`, deploy, then set
`VERTEX_AGENT_ENGINE_ID` — the backend switches over automatically.

## License

MIT — see [LICENSE](LICENSE).
