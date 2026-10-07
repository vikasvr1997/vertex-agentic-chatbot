"""Thin factory for the underlying BigQuery client.

Isolated so the rest of the package depends on an injectable client object
(see ``BigQueryService(..., client=...)``) rather than constructing
``google.cloud.bigquery.Client()`` directly in business logic — the usual
seam for unit testing without live GCP calls, and for an enterprise
environment to swap in a client wrapped with its own auth or interceptors.
"""

from __future__ import annotations

from google.cloud import bigquery


def build_bigquery_client(project_id: str) -> bigquery.Client:
    """Build a BigQuery client for one GCP project.

    No ``location=`` is pinned here: BigQuery resolves each job's region
    from the dataset referenced in the query, which may differ from the
    Vertex AI region used for the agent's own model. Pinning the client to
    one region makes every query against a dataset in a different region
    fail with a 404.
    """
    return bigquery.Client(project=project_id)
