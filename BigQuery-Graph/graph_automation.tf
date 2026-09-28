# 1. Create the governance dataset first: generate_graphs.py reads its
#    graph_schema_state table to detect schema drift, so it must exist
#    before the crawler runs.
resource "google_bigquery_dataset" "global_graph_dataset" {
  dataset_id = "global_governance_ds"
  location   = var.region
}

# 2. Auto-discover every dataset in the project and crawl its schema —
#    no more hardcoded dataset list. Depends on the metadata tables so the
#    schema-drift check has something real to compare against.
data "external" "graph_generator" {
  program = ["python3", "${path.module}/generate_graphs.py"]

  query = {
    project_id = var.project_id
  }

  depends_on = [
    google_bigquery_dataset.global_graph_dataset,
    google_bigquery_table.graph_schema_state,
  ]
}

# 3. Execute the generated local DDL query blocks inside BigQuery. The job
#    id is derived from the DDL content (not uuid()), so a re-apply with an
#    unchanged schema produces no diff and no wasted job run — a new job
#    only fires when the DDL text actually changed.
resource "google_bigquery_job" "deploy_local_graphs" {
  job_id   = "deploy_local_graphs_${substr(sha256(data.external.graph_generator.result["local_ddl_queries"]), 0, 16)}"
  location = var.region

  query {
    query          = data.external.graph_generator.result["local_ddl_queries"]
    use_legacy_sql = false
  }
}

# 4. Execute the overall master graph definition inside the governance
#    dataset, same content-addressed job id scheme as above.
resource "google_bigquery_job" "deploy_master_graph" {
  job_id     = "deploy_master_graph_${substr(sha256(data.external.graph_generator.result["master_ddl_query"]), 0, 16)}"
  location   = var.region
  depends_on = [google_bigquery_job.deploy_local_graphs, google_bigquery_dataset.global_graph_dataset]

  query {
    query          = data.external.graph_generator.result["master_ddl_query"]
    use_legacy_sql = false
  }
}
