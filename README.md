# Agentic Chatbot (Vertex AI)

Enterprise conversational agentic chatbot backed by Google Cloud Vertex AI,
with both Streamlit and Chainlit frontends sharing one authenticated FastAPI
backend.

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

## Project layout

```
src/agentic_chatbot/
  config.py            Settings (env-driven, no API keys)
  logging_config.py    Structured (JSON) logging
  core/
    auth.py            ADC verification
    vertex_client.py   Vertex AI backends (Gemini model / Agent Engine)
    conversation.py    In-memory session tracking
  tools/               Agent-callable tools (function-calling)
  api/                 FastAPI backend (bearer-token authenticated)
  frontend/
    streamlit_app.py
    chainlit_app.py
tests/                 pytest unit + integration tests
openapi/openapi.yaml   Generated OpenAPI spec, audited by 42Crunch in CI
.github/workflows/     CI, SonarQube, 42Crunch, CodeQL
.agents/skills/        Runbooks for coding agents (deploy, add tools, security scans)
```

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
