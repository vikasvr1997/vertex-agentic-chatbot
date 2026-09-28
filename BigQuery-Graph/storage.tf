# Holds the PDF relationship report generated each time a schema change is
# detected (post_apply_report.py). Versioned so old reports stay retrievable
# alongside the matching graph_version_history row.
resource "google_storage_bucket" "graph_reports" {
  name                        = var.report_bucket_name != "" ? var.report_bucket_name : "${var.project_id}-graph-reports"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false

  versioning {
    enabled = true
  }
}
