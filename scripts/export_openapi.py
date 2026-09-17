#!/usr/bin/env python3
"""Export the FastAPI app's OpenAPI schema to openapi/openapi.yaml.

Kept in sync in CI: a diff between the committed spec and a freshly
generated one fails the build, so the 42Crunch security scan always audits
the API surface as it actually is.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agentic_chatbot.api.server import app  # noqa: E402

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "openapi" / "openapi.yaml"


def main() -> None:
    schema = app.openapi()
    OUTPUT_PATH.write_text(yaml.safe_dump(schema, sort_keys=False))
    print(f"Wrote OpenAPI schema to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
