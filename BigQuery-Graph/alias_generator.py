"""Generates alternate names ("aliases") for each table/column so a user who
queries the graph with different terminology than the literal BigQuery names
can still be matched to the right node. Aliases come from Vertex AI Gemini;
if the model call fails (quota, network, missing credentials) we fall back to
a small rule-based synonym expansion so the pipeline never hard-fails on this
enrichment step.
"""

import json
import re
from datetime import UTC, datetime

try:
    import vertexai
    from vertexai.generative_models import GenerativeModel

    _VERTEX_AVAILABLE = True
except ImportError:
    _VERTEX_AVAILABLE = False


_FALLBACK_SYNONYMS = {
    "customer": ["client", "account holder", "buyer", "user"],
    "driver": ["operator", "chauffeur"],
    "trip": ["ride", "journey", "route"],
    "truck": ["vehicle", "lorry"],
    "id": ["identifier", "key", "code"],
    "name": ["title", "label"],
    "date": ["timestamp", "day"],
    "amount": ["total", "value", "sum"],
}


def _fallback_aliases(term):
    words = re.findall(r"[a-z]+", term.lower())
    aliases = set()
    for w in words:
        for syn in _FALLBACK_SYNONYMS.get(w, []):
            aliases.add(term.lower().replace(w, syn))
    return sorted(aliases)


def _ask_gemini(model, table_name, columns):
    prompt = (
        "You generate alternate business-friendly names for a database table "
        "and its columns so a non-technical user's query can still be matched "
        "to the correct table. Return ONLY compact JSON, no prose, in this "
        'exact shape: {"table_aliases": ["..."], "column_aliases": {"<col>": ["..."]}}\n\n'
        f"Table name: {table_name}\n"
        f"Columns: {', '.join(columns)}\n"
        "Give 3-5 aliases per name, covering common synonyms, abbreviations "
        "and industry terms a user might type instead of the literal name."
    )
    response = model.generate_content(prompt)
    text = response.text.strip()
    text = re.sub(r"^```(json)?|```$", "", text, flags=re.MULTILINE).strip()
    return json.loads(text)


def generate_aliases_for_dataset(project_id, dataset_id, tables, vertex_location="us-central1"):
    """tables: {table_name: [column_name, ...]}. Returns rows ready to insert
    into the node_aliases BigQuery table."""
    rows = []
    now = datetime.now(UTC).isoformat()
    model = None
    if _VERTEX_AVAILABLE:
        try:
            vertexai.init(project=project_id, location=vertex_location)
            model = GenerativeModel("gemini-1.5-flash")
        except Exception:
            model = None

    for table_name, columns in tables.items():
        table_aliases, column_aliases = [], {}
        if model is not None:
            try:
                parsed = _ask_gemini(model, table_name, columns)
                table_aliases = parsed.get("table_aliases", [])
                column_aliases = parsed.get("column_aliases", {})
            except Exception:
                model = None  # stop retrying Gemini for the rest of this run

        if table_aliases:
            source = "vertex-ai-gemini"
        else:
            table_aliases = _fallback_aliases(table_name) or [table_name]
            source = "fallback"

        for alias in table_aliases:
            rows.append(
                {
                    "dataset_id": dataset_id,
                    "table_name": table_name,
                    "column_name": None,
                    "alias": alias,
                    "source": source,
                    "generated_at": now,
                }
            )

        for col in columns:
            col_aliases = column_aliases.get(col)
            col_source = source if col_aliases else "fallback"
            for alias in col_aliases or _fallback_aliases(col):
                rows.append(
                    {
                        "dataset_id": dataset_id,
                        "table_name": table_name,
                        "column_name": col,
                        "alias": alias,
                        "source": col_source,
                        "generated_at": now,
                    }
                )
    return rows


def write_aliases(client, project_id, rows):
    if not rows:
        return
    table_ref = f"{project_id}.global_governance_ds.node_aliases"
    errors = client.insert_rows_json(table_ref, rows)
    if errors:
        raise RuntimeError(f"Failed to write node aliases: {errors}")
