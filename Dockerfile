FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --upgrade pip && pip install .

# Non-root user — no service account keys are baked into the image;
# credentials come from ADC (mounted gcloud config locally, or the
# attached service account when running on Cloud Run / GKE).
RUN useradd --create-home appuser
USER appuser

EXPOSE 8080 8501 8000

# Overridden per-service in docker-compose.yml
CMD ["uvicorn", "agentic_chatbot.api.server:app", "--host", "0.0.0.0", "--port", "8080"]
