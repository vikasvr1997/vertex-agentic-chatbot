---
name: run-security-scans
description: Use when asked to run this project's security checks locally (SonarQube static analysis or the 42Crunch OpenAPI audit) before pushing, or when debugging a failing security workflow in CI.
---

# Run security scans locally

This project runs three security-relevant checks in CI
(`.github/workflows/`): `sonarqube.yml`, `42crunch.yml`, and `codeql.yml`.
CodeQL needs no local reproduction (GitHub-hosted only); the other two do.

## SonarQube / SonarCloud

1. Generate coverage: `pytest` (writes `coverage.xml` per `pyproject.toml`).
2. Install `sonar-scanner` (e.g. `brew install sonar-scanner` on macOS).
3. Run: `sonar-scanner -Dsonar.token=$SONAR_TOKEN
   -Dsonar.host.url=$SONAR_HOST_URL`, reading the rest of the config from
   `sonar-project.properties`.
4. In CI, this requires the `SONAR_TOKEN` and `SONAR_HOST_URL` repo secrets
   to be set (Settings → Secrets and variables → Actions). Without them,
   `sonarqube.yml` fails at the scan step — that is expected until a
   SonarQube/SonarCloud project has been provisioned and its token added.

## 42Crunch API security audit

1. Regenerate the OpenAPI spec so the audit reflects the current API
   surface: `python scripts/export_openapi.py` (writes
   `openapi/openapi.yaml`). CI fails the build if this file is stale
   (`git diff --exit-code openapi/openapi.yaml`), so always regenerate
   after touching anything under `src/agentic_chatbot/api/`.
2. Local audit requires a 42Crunch account and its VS Code / CLI tooling,
   or you can push and let `42crunch.yml` run it in CI using the
   `CRUNCH42_API_TOKEN` repo secret.
3. Common failure: 42Crunch penalizes routes without an explicit security
   scheme. Every route in this project other than `/health` must stay
   behind `require_internal_token` (see `api/deps.py`) — don't add new
   unauthenticated routes without a deliberate reason.
