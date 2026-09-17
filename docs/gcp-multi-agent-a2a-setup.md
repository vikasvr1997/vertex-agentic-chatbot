# Building and registering a 3-agent system on GCP (Orchestrator + A2A)

This is the reference for turning the in-process `Orchestrator` /
`BigQueryAgent` in this repo into three independently deployed Vertex AI
agents that talk to each other over the **Agent2Agent (A2A) protocol**:

- **Orchestrator agent** — receives the user's message, decides which
  sub-agent(s) to delegate to, composes the final answer.
- **BigQuery agent** (sub-agent) — read-only NL-to-SQL over BigQuery.
- **GCS agent** (sub-agent) — read-only natural-language queries over
  objects/metadata in Cloud Storage.

APIs and SDK surfaces in this space (ADK, Agent Engine, Agentspace) move
fast. Treat exact method/class names below as "last verified" rather than
guaranteed current — check the linked docs before you build against them.

---

## 1. Concepts, disambiguated

| Term | What it actually is |
|---|---|
| **ADK** (Agent Development Kit) | Google's open-source Python/Java framework for building agents: instructions, tools, sub-agents, sessions, evaluation. This is what you write your agent *code* in. |
| **Agent Engine** (formerly "Reasoning Engine") | The managed Vertex AI runtime you *deploy* an agent to. Handles scaling, session state, and (increasingly) native A2A exposure. This repo's `VertexAgentClient.ReasoningEngineBackend` already talks to one. |
| **A2A (Agent2Agent) protocol** | An open, transport-level protocol (JSON-RPC over HTTP, now under the Linux Foundation) for one agent to discover and call another, regardless of what framework built either side. This is how the Orchestrator calls the BigQuery/GCS agents once they're separate deployments. |
| **MCP (Model Context Protocol)** | A *different* protocol, for connecting an agent to tools/data sources (not agent-to-agent). Not what you need for orchestrator↔sub-agent calls, but relevant if either sub-agent needs to call an external tool server. |
| **Agent Card** | A JSON manifest an A2A-compliant agent serves (conventionally at `/.well-known/agent-card.json`) describing its name, description, and "skills" (capabilities). This is how a caller — the Orchestrator, or a registry — discovers what an agent can do and how to call it. |
| **Agentspace** | Google's enterprise catalog for registering and discovering agents across an organization, with access control. "Registering" an agent in the enterprise sense usually means publishing it here, on top of deploying it to Agent Engine. |

The in-process version you already have (`Orchestrator` calling
`BigQueryAgent.answer()` as a plain Python method) is functionally a
single-agent system with tool-like helpers. Splitting it into three A2A
agents only pays off once you need independent deployment/scaling,
per-agent IAM isolation, or reuse of a sub-agent by *other* orchestrators —
don't do it just for its own sake.

---

## 2. Prerequisites

```bash
gcloud services enable \
  aiplatform.googleapis.com \
  bigquery.googleapis.com \
  storage.googleapis.com \
  run.googleapis.com \
  --project=<project-id>

pip install google-adk google-cloud-aiplatform[agent_engines]
```

Each agent gets its **own service account** — this is the actual security
boundary, more than the code:

```bash
for sa in orchestrator-agent bigquery-agent gcs-agent; do
  gcloud iam service-accounts create "$sa" --project=<project-id>
done
```

| Agent | Roles (least privilege) |
|---|---|
| `bigquery-agent` | `roles/bigquery.dataViewer` (on the target dataset only), `roles/bigquery.jobUser` (on the project) — nothing else. Never `dataEditor`/`admin`. |
| `gcs-agent` | `roles/storage.objectViewer` (on the target bucket(s) only). Never `objectAdmin`/`legacyBucketWriter`. |
| `orchestrator-agent` | `roles/aiplatform.user` (to invoke Agent Engine endpoints / call the other agents). It does **not** need BigQuery or GCS roles at all — it never touches data directly, only delegates. |

```bash
gcloud projects add-iam-policy-binding <project-id> \
  --member="serviceAccount:bigquery-agent@<project-id>.iam.gserviceaccount.com" \
  --role="roles/bigquery.jobUser"

bq add-iam-policy-binding \
  --member="serviceAccount:bigquery-agent@<project-id>.iam.gserviceaccount.com" \
  --role="roles/bigquery.dataViewer" \
  <project-id>:<dataset>

gcloud storage buckets add-iam-policy-binding gs://<bucket> \
  --member="serviceAccount:gcs-agent@<project-id>.iam.gserviceaccount.com" \
  --role="roles/storage.objectViewer"
```

---

## 3. Build each sub-agent with ADK

Each sub-agent is a small ADK `LlmAgent` wrapping the same guardrailed
logic already in this repo — you're porting `BigQueryAgent`/`BigQueryService`
into an ADK tool function, not rewriting the guardrail.

```python
# bigquery_agent/agent.py
from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from agentic_chatbot.services.bigquery_service import BigQueryService, ReadOnlyQueryError

def run_readonly_query(sql: str) -> dict:
    """Execute a read-only BigQuery SQL query and return the resulting rows.

    Only SELECT/WITH statements are permitted; anything else is rejected.
    """
    service = BigQueryService(settings=...)
    try:
        return {"rows": service.run_query(sql)}
    except ReadOnlyQueryError as exc:
        return {"error": str(exc)}

root_agent = Agent(
    name="bigquery_data_agent",
    model="gemini-2.5-flash",
    description="Answers questions by generating and running read-only BigQuery SQL.",
    instruction=(
        "You have read-only access to BigQuery via the run_readonly_query tool. "
        "Only ever produce SELECT/WITH statements. If a question can't be answered "
        "with a read-only query, say so instead of guessing."
    ),
    tools=[FunctionTool(run_readonly_query)],
)
```

```python
# gcs_agent/agent.py
from google.adk.agents import Agent
from google.adk.tools import FunctionTool
from google.cloud import storage

def list_objects(bucket: str, prefix: str = "") -> dict:
    """List object names/sizes/updated-times under a prefix in a GCS bucket. Read-only."""
    client = storage.Client()  # ADC
    blobs = client.list_blobs(bucket, prefix=prefix, max_results=200)
    return {"objects": [{"name": b.name, "size": b.size, "updated": str(b.updated)} for b in blobs]}

def read_object_text(bucket: str, name: str, max_bytes: int = 200_000) -> dict:
    """Read up to max_bytes of a text object's content. Read-only; no writes/deletes exposed."""
    client = storage.Client()
    blob = client.bucket(bucket).blob(name)
    return {"content": blob.download_as_text()[:max_bytes]}

root_agent = Agent(
    name="gcs_data_agent",
    model="gemini-2.5-flash",
    description="Answers questions about files/objects stored in Cloud Storage (read-only).",
    instruction="You can list and read objects in the configured bucket(s). Never claim write/delete ability.",
    tools=[FunctionTool(list_objects), FunctionTool(read_object_text)],
)
```

Test each one locally before deploying anything:

```bash
adk run bigquery_agent
adk web   # local dev UI to chat with an agent and inspect tool calls
```

---

## 4. Deploy each sub-agent to Vertex AI Agent Engine

```python
import vertexai
from vertexai import agent_engines

vertexai.init(project="<project-id>", location="us-central1", staging_bucket="gs://<staging-bucket>")

remote_bigquery_agent = agent_engines.create(
    bigquery_agent.root_agent,
    requirements=["google-cloud-aiplatform[agent_engines,adk]", "google-cloud-bigquery"],
    service_account="bigquery-agent@<project-id>.iam.gserviceaccount.com",
)
print(remote_bigquery_agent.resource_name)
```

Repeat for the GCS agent with its own service account. Each deployment
gets a `resource_name` (`projects/.../locations/.../reasoningEngines/...`)
— save both; the orchestrator needs them next.

---

## 5. Wire up A2A: expose each sub-agent's Agent Card, then call it from the Orchestrator

An ADK agent deployed to Agent Engine can be exposed as an A2A server —
consult the current ADK docs for the exact helper (`to_a2a()` /
equivalent) as this surface has changed shape a few times; conceptually it
wraps your `root_agent` in an A2A-compliant HTTP server that serves:

- `GET /.well-known/agent-card.json` — the discovery manifest
- the A2A task endpoints (`tasks/send`, `tasks/sendSubscribe`, etc.) that
  actually run a turn

On the Orchestrator side, ADK provides a remote-agent construct (commonly
`RemoteA2aAgent` or similar — again, check current ADK naming) that takes
the sub-agent's Agent Card URL and lets you add it to `sub_agents=[...]`
exactly like a local agent:

```python
# orchestrator_agent/agent.py
from google.adk.agents import Agent
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent  # verify current import path

bigquery_agent = RemoteA2aAgent(
    name="bigquery_data_agent",
    agent_card_url="https://<bigquery-agent-a2a-endpoint>/.well-known/agent-card.json",
)
gcs_agent = RemoteA2aAgent(
    name="gcs_data_agent",
    agent_card_url="https://<gcs-agent-a2a-endpoint>/.well-known/agent-card.json",
)

root_agent = Agent(
    name="orchestrator",
    model="gemini-2.5-flash",
    description="Routes user questions to the BigQuery or GCS data agent, or answers directly.",
    instruction=(
        "Delegate structured/data questions about tables to bigquery_data_agent, "
        "questions about files/objects to gcs_data_agent, and answer anything else yourself."
    ),
    sub_agents=[bigquery_agent, gcs_agent],
)
```

Deploy the orchestrator the same way (§4), with the `orchestrator-agent`
service account. At runtime it calls each sub-agent's A2A endpoint over
HTTPS using its own identity — this is the point where the "1 orchestrator,
2 sub-agents" shape becomes real, independently-scalable services instead
of Python function calls in one process.

---

## 6. "Registering" the agents

Two levels, pick based on who needs to find these agents:

1. **Machine-to-machine (minimum viable):** the orchestrator just needs the
   two Agent Card URLs — store them as config (env vars / Secret Manager),
   the same way this repo's `.env` holds `VERTEX_AGENT_ENGINE_ID` today.
   No separate "registry" is required for the orchestrator to function.
2. **Organization-wide discovery (Agentspace):** if you want other teams'
   agents/users to find and invoke the BigQuery or GCS agent without
   knowing the orchestrator exists, register each Agent Card in
   **Agentspace** (Console → Agentspace → Agents → Register), which adds
   access control, a human-facing catalog entry, and discovery by
   capability rather than by hardcoded URL. This is the "modern,
   registered agent" version of what used to just be a deployed endpoint.

---

## 7. Rollout checklist

- [ ] Per-agent service account created, least-privilege roles granted (§2)
- [ ] Each sub-agent tested locally with `adk web`/`adk run` before deploy
- [ ] Each sub-agent deployed to Agent Engine with its own service account
- [ ] A2A exposure verified: `curl https://<endpoint>/.well-known/agent-card.json`
      returns the expected manifest for each sub-agent
- [ ] Orchestrator's `sub_agents=[...]` points at the correct Agent Card URLs
      and deploys with the `orchestrator-agent` (data-role-less) service account
- [ ] End-to-end smoke test: ask the orchestrator a BigQuery-shaped question
      and a GCS-shaped question, confirm each routes correctly
- [ ] Guardrails re-verified post-deploy: attempt a write-shaped question
      against the BigQuery agent and confirm it's refused, same as
      `tests/unit/test_bigquery_service.py` in this repo does in-process
- [ ] Observability: Cloud Trace/Logging enabled for all three deployments
      so a cross-agent A2A call chain is traceable end-to-end
- [ ] (If using Agentspace) each Agent Card registered with correct access
      control for who's allowed to invoke it directly

## References

- Agent Development Kit (ADK): https://google.github.io/adk-docs/
- A2A protocol spec: https://a2aproject.github.io/A2A/
- Vertex AI Agent Engine: https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/overview
- Agentspace: https://cloud.google.com/products/agentspace
