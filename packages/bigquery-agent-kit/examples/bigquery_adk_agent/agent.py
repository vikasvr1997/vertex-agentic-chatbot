"""ADK CLI entry point for the reusable BigQuery agent kit."""

from bigquery_agent_kit import BigQueryAgentConfig, create_bigquery_agent

root_agent = create_bigquery_agent(BigQueryAgentConfig.from_env())
