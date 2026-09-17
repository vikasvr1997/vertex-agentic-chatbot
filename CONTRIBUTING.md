# Contributing

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
make dev-install       # installs package + dev deps, installs pre-commit hooks
gcloud auth application-default login
gcloud config set project <your-gcp-project-id>
cp .env.example .env   # fill in GOOGLE_CLOUD_PROJECT at minimum
```

## Everyday commands

| Command | What it does |
|---|---|
| `make lint` | Ruff lint |
| `make format` | Ruff autoformat |
| `make typecheck` | Mypy |
| `make test` | Pytest with coverage |
| `make run-api` | FastAPI backend on :8080 |
| `make run-streamlit` | Streamlit frontend on :8501 |
| `make run-chainlit` | Chainlit frontend on :8000 |
| `make docker-up` | All three services via docker-compose |
| `make openapi` | Regenerate `openapi/openapi.yaml` after API changes |

## Before opening a PR

1. `make lint && make typecheck && make test` all pass.
2. If you touched `src/agentic_chatbot/api/`, run `make openapi` and commit
   the regenerated spec — CI fails on drift.
3. Fill out the PR template's test plan honestly; the security-considerations
   section matters for anything touching auth or the API surface.

See `.agents/skills/` for step-by-step runbooks on deploying to Vertex AI
Agent Engine, adding agent tools, and running the security scans locally.
