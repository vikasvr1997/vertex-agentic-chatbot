---
name: deploy-to-vertex-agent-engine
description: Use when the agent's logic (tools, reasoning loop) is ready to move from local Gemini-model development to a deployed Vertex AI Agent Engine (Reasoning Engine).
---

# Deploy to Vertex AI Agent Engine

This project talks to Vertex AI two ways, selected by `VERTEX_AGENT_ENGINE_ID`
in `.env` (see `src/agentic_chatbot/core/vertex_client.py`):

- **Empty** → `GeminiModelBackend`, a direct chat session against a Gemini
  model. This is the default for local development.
- **Set** → `ReasoningEngineBackend`, which queries a deployed Agent Engine.

## Steps

1. Confirm ADC is configured: `gcloud auth application-default login` and
   `gcloud config set project <project-id>`. Never introduce an API-key
   based auth path — this project intentionally forbids it.
2. Build your agent (reasoning loop + tools) using the Vertex AI Agent
   Engine SDK (`vertexai.preview.reasoning_engines`). Register any callable
   tools from `src/agentic_chatbot/tools/registry.py` — use `list_tools()`
   to get the full set to hand to the agent's tool config.
3. Deploy:
   ```python
   from vertexai.preview import reasoning_engines

   remote_agent = reasoning_engines.ReasoningEngine.create(
       your_local_agent_instance,
       requirements=["google-cloud-aiplatform[reasoningengine]"],
   )
   print(remote_agent.resource_name)
   ```
4. Set `VERTEX_AGENT_ENGINE_ID` in `.env` (and in your deployment's secret
   manager / env config) to the printed `resource_name`
   (`projects/.../locations/.../reasoningEngines/...`).
5. Restart the API service. No code changes are needed — `VertexAgentClient`
   picks `ReasoningEngineBackend` automatically once the ID is set.
6. Verify: `curl -H "Authorization: Bearer $INTERNAL_API_TOKEN" -X POST
   localhost:8080/chat -d '{"message":"hello"}' -H 'Content-Type: application/json'`
   and confirm the reply comes from the deployed agent (check Cloud Logging
   for the Agent Engine resource to be sure it's actually being hit).
7. Run `pytest` — the existing backend-selection tests in
   `tests/unit/test_vertex_client.py` should still pass unchanged; add a
   new test case if the deployed agent's response shape differs from the
   `{"output": ...}` convention assumed by `_extract_text`.
