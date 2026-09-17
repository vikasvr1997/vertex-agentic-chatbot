# Security Policy

## Reporting a Vulnerability

Please report suspected vulnerabilities privately via GitHub's
[Security Advisories](../../security/advisories/new) for this repository
rather than opening a public issue. You should expect an initial response
within 5 business days.

## Design principles this project follows

- **No Vertex AI / Gemini API keys.** Authentication to Vertex AI is
  exclusively via Application Default Credentials (ADC) — see
  `src/agentic_chatbot/core/auth.py`. Do not add API-key based auth paths.
- **Internal API is authenticated.** Every route besides `/health` requires
  a bearer token (`INTERNAL_API_TOKEN`), checked with a constant-time
  comparison in `api/deps.py`.
- **No secrets committed.** `.gitignore` blocks `.env`, service-account key
  files, and `*.pem`. If you find a credential committed to history,
  rotate it immediately and report via the process above.
- **Automated scanning.** `.github/workflows/codeql.yml` (static analysis),
  `sonarqube.yml` (code quality/security hotspots), and `42crunch.yml`
  (OpenAPI-level API security audit) run on every push/PR to `main`.
