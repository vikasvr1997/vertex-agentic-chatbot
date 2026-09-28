# Cloud Build trigger that runs `terraform apply` (re-crawling every dataset
# and regenerating DDL if anything changed) followed by post_apply_report.py
# (PDF + aliases + version history + email alert). Defined manually (no repo
# push event) so it only ever fires when Cloud Scheduler calls it below.
resource "google_cloudbuild_trigger" "graph_rebuild" {
  name     = "daily-graph-rebuild"
  location = "global"

  source_to_build {
    uri       = var.cloudbuild_repo_uri
    ref       = "refs/heads/main"
    repo_type = "GITHUB"
  }

  filename = "vertex-agentic-chatbot/BigQuery-Graph/cloudbuild.yaml"

  substitutions = {
    _ALERT_EMAIL = var.alert_email
  }
}

# Dedicated service account Cloud Scheduler uses to invoke the trigger.
# Kept separate from the Cloud Build default SA so the "who can kick off a
# rebuild" permission is auditable on its own.
resource "google_service_account" "graph_scheduler_sa" {
  account_id   = "graph-scheduler-invoker"
  display_name = "Invokes the daily BigQuery graph rebuild Cloud Build trigger"
}

resource "google_project_iam_member" "scheduler_can_run_builds" {
  project = var.project_id
  role    = "roles/cloudbuild.builds.editor"
  member  = "serviceAccount:${google_service_account.graph_scheduler_sa.email}"
}

# Fires every morning (default 7am, see var.scheduler_cron) and runs the
# trigger above via the Cloud Build REST API.
resource "google_cloud_scheduler_job" "daily_graph_check" {
  name      = "daily-graph-schema-check"
  schedule  = var.scheduler_cron
  time_zone = var.scheduler_timezone
  region    = var.vertex_ai_location

  http_target {
    http_method = "POST"
    uri         = "https://cloudbuild.googleapis.com/v1/projects/${var.project_id}/locations/global/triggers/${google_cloudbuild_trigger.graph_rebuild.trigger_id}:run"
    body        = base64encode(jsonencode({ branchName = "main" }))

    oauth_token {
      service_account_email = google_service_account.graph_scheduler_sa.email
    }
  }
}

# --- IAM for the Cloud Build service account that actually runs
# terraform apply + post_apply_report.py inside cloudbuild.yaml ---

data "google_project" "current" {
  project_id = var.project_id
}

locals {
  cloudbuild_sa = "serviceAccount:${data.google_project.current.number}@cloudbuild.gserviceaccount.com"
}

resource "google_project_iam_member" "cloudbuild_bigquery_data_editor" {
  project = var.project_id
  role    = "roles/bigquery.dataEditor"
  member  = local.cloudbuild_sa
}

resource "google_project_iam_member" "cloudbuild_bigquery_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = local.cloudbuild_sa
}

resource "google_project_iam_member" "cloudbuild_vertex_ai_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = local.cloudbuild_sa
}

resource "google_storage_bucket_iam_member" "cloudbuild_report_bucket_writer" {
  bucket = google_storage_bucket.graph_reports.name
  role   = "roles/storage.objectAdmin"
  member = local.cloudbuild_sa
}

# --- SMTP credentials for post_apply_report.py's alert email. Terraform
# only creates the secret containers; add the actual values out of band
# (`gcloud secrets versions add smtp_host --data-file=-`, etc.) so
# credentials never sit in Terraform state or source control. ---

resource "google_secret_manager_secret" "smtp_host" {
  secret_id = "smtp_host"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "smtp_user" {
  secret_id = "smtp_user"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "smtp_password" {
  secret_id = "smtp_password"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "cloudbuild_reads_smtp_host" {
  secret_id = google_secret_manager_secret.smtp_host.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = local.cloudbuild_sa
}

resource "google_secret_manager_secret_iam_member" "cloudbuild_reads_smtp_user" {
  secret_id = google_secret_manager_secret.smtp_user.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = local.cloudbuild_sa
}

resource "google_secret_manager_secret_iam_member" "cloudbuild_reads_smtp_password" {
  secret_id = google_secret_manager_secret.smtp_password.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = local.cloudbuild_sa
}
