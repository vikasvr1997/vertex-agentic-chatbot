# Production-readiness TODOs

The BigQuery agent itself (NL -> SQL, read-only guardrail, IAM) works and
is tested. These are the gaps between that and a deployment that holds up
past one instance / one trusted user. Roughly ordered by how quickly each
one actually breaks something.

## Breaks at more than one instance or one user

- [ ] **Externalize session storage.** `core/conversation.py`'s
      `ConversationManager` is in-memory, single-process. A restart drops
      every conversation; running 2+ API replicas splits users across
      independent stores with no shared history. Move to Firestore or
      Redis behind the same `get_or_create`/`touch`/`clear` interface.
- [ ] **Per-user identity.** `INTERNAL_API_TOKEN` is one shared secret —
      every caller is indistinguishable. Needed for: audit logging (who
      asked what), rate limiting per user instead of globally, and any
      future column/row-level access control tied to identity. Options:
      IAP in front of Cloud Run, or OAuth/OIDC at the frontend with the
      user's identity forwarded to the backend and checked per-request.
- [ ] **Rate limiting / abuse control.** Nothing stops one caller from
      firing many expensive queries per minute.
      `bigquery_max_bytes_billed` caps cost *per query*, not in aggregate.
      Add per-user/IP rate limiting (API gateway, or `slowapi`/Redis token
      bucket in FastAPI) before this is reachable by more than a
      handful of trusted users.

## Correctness and safety beyond the SQL guardrail

- [ ] **Evaluation harness, not just unit tests.** Current tests prove the
      guardrail and plumbing work; they don't prove the generated SQL is
      *correct* for real questions. Build a golden set (question ->
      expected SQL or expected answer) and run it whenever the prompt or
      model changes — Vertex AI's Gen AI evaluation service is the
      natural fit. Catches silent regressions that pytest can't.
- [ ] **Treat retrieved rows as untrusted content.** The model reads back
      data it just queried; if a table contains attacker-influenced text
      (e.g. a free-text column), a later turn's prompt includes that text
      verbatim — indirect prompt injection via data, not chat input. The
      write guardrail still blocks any resulting bad query at execution
      time, but the model's *answers* could still be steered. Make the
      system prompt explicit that retrieved row content is data, never
      instructions.
- [ ] **Column/row-level data governance.** Dataset-level IAM
      (`roles/bigquery.dataViewer`) doesn't restrict individual columns.
      If the dataset has sensitive/PII fields, add BigQuery column-level
      security policies or authorized views so a syntactically read-only
      query still can't surface restricted data.

## Operability

- [ ] **Tracing and alerting, not just structured logs.** No visibility
      today into the FastAPI -> Vertex AI -> BigQuery call chain latency,
      no dashboards, no alerts on error rate, BigQuery spend, or Vertex AI
      quota usage. Add Cloud Trace + a few Cloud Monitoring alert
      policies.
- [ ] **Real deployment topology.** `docker-compose.yml` is a local dev
      convenience. Still needed: target platform (Cloud Run vs GKE), a CD
      pipeline (build/push/deploy on merge to `main`, beyond the existing
      CI), secrets in Secret Manager instead of `.env`, and a staging
      environment to validate against before prod.

## Not urgent, but worth tracking

- [ ] Fallback behavior when Vertex AI or BigQuery is degraded/unavailable
      (circuit breaker / graceful error message vs. raw exception).
- [ ] User feedback loop on answers (thumbs up/down) to build the golden
      eval set from real usage instead of only hand-written cases.
- [ ] Incident-response runbook once this has real users depending on it.
