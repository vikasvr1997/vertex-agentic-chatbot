---
name: add-agent-tool
description: Use when asked to add a new callable tool/function for the agent (e.g. a data lookup, an external API call) to this chatbot.
---

# Add a new agent tool

Tools live in `src/agentic_chatbot/tools/` and are registered with the
`@tool` decorator from `tools/registry.py` (see `example_tools.py` for the
pattern).

## Steps

1. Add the function to `tools/example_tools.py`, or create a new module
   under `tools/` for a distinct domain (e.g. `tools/billing_tools.py`) and
   import it wherever tools are collected before an agent build/deploy.
2. Requirements enforced by the registry:
   - Type-annotated parameters and return type (Vertex AI Agent Engine's
     function-calling relies on the signature to build the tool schema).
   - A docstring — `@tool` raises `ValueError` without one, since the
     docstring is what the model sees to decide when to call the tool.
3. Keep tools side-effect-aware: if a tool calls an external system, log
   inputs/outputs via `agentic_chatbot.logging_config.get_logger`, and
   consider whether it needs its own retry/timeout handling separate from
   the Vertex AI call retries already in `core/vertex_client.py`.
4. Write a unit test in `tests/unit/test_tools_registry.py` (or a new test
   file for a larger tool module) that calls the tool function directly —
   no need to go through Vertex AI to test tool logic in isolation.
5. If an Agent Engine is already deployed
   ([[deploy-to-vertex-agent-engine]]), the new tool must also be added to
   that deployed agent's tool config and redeployed — adding it to the
   registry alone only affects local `GeminiModelBackend` development
   unless you wire function-calling into that path too.
