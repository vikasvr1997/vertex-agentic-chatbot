# Tracks the last-seen schema hash so generate_graphs.py can tell whether
# today's run found any table/column changes since yesterday's.
resource "google_bigquery_table" "graph_schema_state" {
  dataset_id = google_bigquery_dataset.global_graph_dataset.dataset_id
  table_id   = "graph_schema_state"

  schema = jsonencode([
    { name = "schema_hash", type = "STRING", mode = "REQUIRED" },
    { name = "checked_at", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "schema_changed", type = "BOOLEAN", mode = "REQUIRED" },
  ])

  deletion_protection = false
}

# Every generated graph version's DDL is preserved here, so a bad
# auto-generated version can be rolled back to any prior one with
# rollback.py instead of losing it once CREATE OR REPLACE overwrites it.
resource "google_bigquery_table" "graph_version_history" {
  dataset_id = google_bigquery_dataset.global_graph_dataset.dataset_id
  table_id   = "graph_version_history"

  schema = jsonencode([
    { name = "version", type = "INTEGER", mode = "REQUIRED" },
    { name = "created_at", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "schema_hash", type = "STRING", mode = "REQUIRED" },
    { name = "local_ddl", type = "STRING", mode = "REQUIRED" },
    { name = "master_ddl", type = "STRING", mode = "REQUIRED" },
    { name = "pdf_report_uri", type = "STRING", mode = "NULLABLE" },
    { name = "is_current", type = "BOOLEAN", mode = "REQUIRED" },
  ])

  deletion_protection = false
}

# AI-generated (and fallback) alternate names per table/column, so a user
# query using different terminology than the literal schema can still be
# resolved via alias_resolver.py.
resource "google_bigquery_table" "node_aliases" {
  dataset_id = google_bigquery_dataset.global_graph_dataset.dataset_id
  table_id   = "node_aliases"

  schema = jsonencode([
    { name = "dataset_id", type = "STRING", mode = "REQUIRED" },
    { name = "table_name", type = "STRING", mode = "REQUIRED" },
    { name = "column_name", type = "STRING", mode = "NULLABLE" },
    { name = "alias", type = "STRING", mode = "REQUIRED" },
    { name = "source", type = "STRING", mode = "REQUIRED" },
    { name = "generated_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])

  deletion_protection = false
}
