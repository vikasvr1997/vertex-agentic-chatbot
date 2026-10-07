"""Factories for clients to external systems (currently: BigQuery)."""

from bigquery_agent_kit.clients.bigquery_client import build_bigquery_client

__all__ = ["build_bigquery_client"]
