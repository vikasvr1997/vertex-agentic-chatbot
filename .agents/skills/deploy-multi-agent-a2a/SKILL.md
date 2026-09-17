---
name: deploy-multi-agent-a2a
description: Use when asked to split the orchestrator/BigQuery agent into separately deployed GCP agents (Orchestrator + BigQuery + GCS) that talk over the A2A protocol, or to add a third data-source sub-agent to that shape.
---

# Deploy the Orchestrator + BigQuery + GCS agents as separate A2A services

Full detail, IAM tables, and code sketches: `docs/gcp-multi-agent-a2a-setup.md`.
This is the condensed action sequence.

1. Enable APIs and create one service account per agent (never share one
   across agents — that's the whole point of splitting them):
   `orchestrator-agent`, `bigquery-agent`, `gcs-agent`.
2. Grant each service account only what it needs:
   - `bigquery-agent`: `roles/bigquery.dataViewer` on the dataset,
     `roles/bigquery.jobUser` on the project. Nothing else, ever.
   - `gcs-agent`: `roles/storage.objectViewer` on the target bucket(s).
   - `orchestrator-agent`: `roles/aiplatform.user` only — it delegates,
     it never touches BigQuery/GCS directly.
3. Port this repo's guardrailed logic into ADK agents, don't rewrite it:
   - `BigQueryService.run_query` (already SELECT/WITH-only) becomes an ADK
     `FunctionTool` on a new `bigquery_data_agent`.
   - A new read-only GCS tool (list/read objects, no write/delete methods
     exposed at all) becomes a `FunctionTool` on a new `gcs_data_agent`.
   - Test each locally with `adk run <agent_dir>` / `adk web` before
     deploying anything.
4. Deploy each sub-agent to Vertex AI Agent Engine
   (`vertexai.agent_engines.create(...)`) using its own service account.
   Save each `resource_name`.
5. Expose each sub-agent over A2A and get its Agent Card URL
   (`https://<endpoint>/.well-known/agent-card.json`). Verify with `curl`
   that the manifest is actually served before wiring the orchestrator to
   it — a wrong URL fails silently as "agent has no tools" otherwise.
6. Build the orchestrator ADK agent with `sub_agents=[RemoteA2aAgent(...),
   RemoteA2aAgent(...)]` pointing at those two Agent Card URLs, and deploy
   it with the `orchestrator-agent` service account.
7. Smoke-test end-to-end: one BigQuery-shaped question, one GCS-shaped
   question, and one write-attempt question (should be refused by the
   BigQuery agent's guardrail, same as `tests/unit/test_bigquery_service.py`
   asserts in-process).
8. If other teams/agents need to discover these sub-agents beyond this one
   orchestrator, register their Agent Cards in **Agentspace**
   (Console → Agentspace → Agents → Register) rather than only handing the
   URL to the orchestrator's config.

Don't do this split until it's actually needed (independent scaling,
per-agent IAM isolation, or reuse by another orchestrator) — the in-process
`Orchestrator`/`BigQueryAgent` in `src/agentic_chatbot/agents/` already
gives the same guardrails and behavior with far less operational surface.
