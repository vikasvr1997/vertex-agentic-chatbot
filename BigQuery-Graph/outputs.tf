output "project_id" {
  value = var.project_id
}

output "schema_hash" {
  value = data.external.graph_generator.result["schema_hash"]
}

output "schema_changed" {
  value = data.external.graph_generator.result["schema_changed"]
}

output "local_ddl_by_location" {
  value = local.local_ddl_by_location
}

output "master_ddl_query" {
  value = data.external.graph_generator.result["master_ddl_query"]
}

output "datasets_excluded_from_master" {
  value       = data.external.graph_generator.result["datasets_excluded_from_master_json"]
  description = "Datasets whose location differs from var.region (the governance dataset's location) and so couldn't be folded into the master graph"
}

output "relationship_report_json" {
  value = data.external.graph_generator.result["relationship_report_json"]
}

output "discovered_datasets" {
  value = data.external.graph_generator.result["datasets_json"]
}

output "report_bucket" {
  value = google_storage_bucket.graph_reports.name
}
