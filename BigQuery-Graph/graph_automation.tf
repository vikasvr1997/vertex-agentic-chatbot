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
    project_id          = var.project_id
    governance_location = var.region
  }

  depends_on = [
    google_bigquery_dataset.global_graph_dataset,
    google_bigquery_table.graph_schema_state,
  ]
}

locals {
  # A BigQuery job (and any property graph it creates) can only reference
  # datasets that live in the SAME location as the job itself, so datasets
  # spread across regions need one deploy job per region -- a single job
  # at var.region can't touch a dataset that lives elsewhere.
  local_ddl_by_location = jsondecode(data.external.graph_generator.result["local_ddl_by_location_json"])
}

# 3. Execute the generated local DDL query blocks inside BigQuery, one job
#    per dataset location. The job id is derived from the DDL content (not
#    uuid()), so a re-apply with an unchanged schema produces no diff and no
#    wasted job run — a new job only fires when the DDL text actually
#    changed. create_disposition and write_disposition must be explicitly
#    blanked out: BigQuery rejects a script/DDL statement (CREATE OR REPLACE
#    PROPERTY GRAPH counts as one) if either is set, but the provider sends
#    its non-empty defaults unless told otherwise — "configuration.query.
#    createDisposition cannot be set for scripts".
resource "google_bigquery_job" "deploy_local_graphs" {
  for_each = local.local_ddl_by_location

  job_id   = "deploy_local_graphs_${replace(each.key, "-", "")}_${substr(sha256("${each.value}|v2"), 0, 16)}"
  location = each.key

  query {
    query              = each.value
    use_legacy_sql     = false
    create_disposition = ""
    write_disposition  = ""
  }
}

# 4. Execute the overall master graph definition inside the governance
#    dataset. Only datasets in var.region (the governance dataset's own
#    location) are folded in here — generate_graphs.py reports anything
#    excluded for being in a different region via
#    datasets_excluded_from_master_json (see outputs.tf).
resource "google_bigquery_job" "deploy_master_graph" {
  job_id     = "deploy_master_graph_${substr(sha256("${data.external.graph_generator.result["master_ddl_query"]}|v2"), 0, 16)}"
  location   = var.region
  depends_on = [google_bigquery_job.deploy_local_graphs, google_bigquery_dataset.global_graph_dataset]

  query {
    query              = data.external.graph_generator.result["master_ddl_query"]
    use_legacy_sql     = false
    create_disposition = ""
    write_disposition  = ""
  }
}
