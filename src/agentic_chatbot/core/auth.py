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
