"""Developer-only tooling — not part of the production chat product.

`architecture_flow_tool` is a self-contained module (data model, in-memory
store, validator, deterministic trace chat, FastAPI router, and a
Dash + dash-cytoscape UI) mounted into the main FastAPI app — see
`api/server.py`. It answers from whichever architecture/service is
currently registered on it; nothing in it is specific to this project's
own domain.
"""

from __future__ import annotations
