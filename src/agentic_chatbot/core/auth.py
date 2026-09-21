"""Application Default Credentials (ADC) verification.

This project never reads or accepts a Vertex AI / Gemini API key. All
connectivity to Vertex AI goes through ADC, resolved in this order by
google-auth:

  1. GOOGLE_APPLICATION_CREDENTIALS env var (path to a service account key),
     used for local containers / CI if explicitly configured.
  2. `gcloud auth application-default login` user credentials (local dev).
  3. The attached service account when running on Cloud Run / GCE / GKE.

Local setup:
    gcloud auth application-default login
    gcloud config set project <your-project-id>
"""

from __future__ import annotations

import google.auth
from google.auth.exceptions import DefaultCredentialsError


class AdcNotConfiguredError(RuntimeError):
    """Raised when no Application Default Credentials can be resolved."""


class BigQueryAccessError(RuntimeError):
    """Raised when the ADC identity can't reach BigQuery or a configured dataset."""


def ensure_adc() -> None:
    """Fail fast with a clear remediation message if ADC is not configured."""
    try:
        google.auth.default()
    except DefaultCredentialsError as exc:
        raise AdcNotConfiguredError(
            "No Application Default Credentials found. Run "
            "`gcloud auth application-default login` for local development, "
            "or attach a service account when deploying to Cloud Run / GKE. "
            "This project does not support Vertex AI API-key authentication."
        ) from exc


def ensure_bigquery_access(project_id: str, dataset: str) -> None:
    """Fail fast if the ADC identity can't reach the configured BigQuery dataset.

    Mirrors ``ensure_adc()``: called once, eagerly, from the one place that
    constructs a real (non-test) ``BigQueryService`` client, so a missing
    dataset or IAM grant surfaces immediately instead of on the first user
    query.
    """
    from google.api_core.exceptions import NotFound, PermissionDenied
    from google.cloud import bigquery

    client = bigquery.Client(project=project_id)
    try:
        client.get_dataset(dataset)
    except NotFound as exc:
        raise BigQueryAccessError(
            f"BigQuery dataset '{dataset}' not found in project '{project_id}'."
        ) from exc
    except PermissionDenied as exc:
        raise BigQueryAccessError(
            f"Permission denied for BigQuery dataset '{dataset}' in project "
            f"'{project_id}'. Grant the ADC identity 'roles/bigquery.dataViewer' "
            "on the dataset and 'roles/bigquery.jobUser' on the project."
        ) from exc


def get_secret(project_id: str, secret_id: str, version_id: str = "latest") -> str:
    """Retrieve a secret's value from Google Cloud Secret Manager.

    Used for credentials that aren't Vertex AI/Gemini access — e.g. a
    third-party model API key (see ``core.vertex_client.ClaudeModelBackend``)
    — which stay out of ``.env`` on purpose. Access to Secret Manager itself
    is via ADC, so no separate credential is needed to call this.
    """
    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_id}/versions/{version_id}"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8")
