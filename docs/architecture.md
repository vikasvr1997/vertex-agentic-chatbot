# Architecture

```
┌─────────────┐     ┌─────────────┐
│  Streamlit  │     │  Chainlit   │
│  (:8501)    │     │  (:8000)    │
└──────┬──────┘     └──────┬──────┘
       │  HTTPS + Bearer   │
       └─────────┬─────────┘
                  ▼
          ┌───────────────┐
          │  FastAPI API  │   (:8080, INTERNAL_API_TOKEN)
          │  /chat /health│
          └───────┬───────┘
                  │  ADC (no API key)
                  ▼
        ┌───────────────────┐
        │     Vertex AI      │
        │  Gemini model  or  │
        │  Agent Engine       │
        └───────────────────┘
```

## Why a shared backend instead of each frontend calling Vertex AI directly

- **One place for credentials and auth.** ADC verification
  (`core/auth.py`) and Vertex AI client construction happen once, in the
  API process. Frontends never touch Google Cloud credentials.
- **One place for session state.** `core/conversation.py` tracks sessions
  centrally, so a user could in principle switch between Streamlit and
  Chainlit mid-conversation using the same `session_id`.
- **Independent scaling.** The API, Streamlit, and Chainlit processes can
  be deployed and scaled separately (see `docker-compose.yml` — three
  services, one image).

## Backend switch: Gemini model → Agent Engine

`core/vertex_client.VertexAgentClient` picks between `GeminiModelBackend`
and `ReasoningEngineBackend` based solely on whether
`Settings.vertex_agent_engine_id` is set. See
`.agents/skills/deploy-to-vertex-agent-engine/SKILL.md` for the deployment
runbook — no code changes are required to switch over.
