terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

variable "project_id" {
  type        = string
  description = "Your active Google Cloud Project ID"
}

variable "region" {
  type    = string
  default = "US"
}

variable "alert_email" {
  type        = string
  description = "Email address notified when a new graph version is generated after a schema change"
}

variable "report_bucket_name" {
  type        = string
  default     = ""
  description = "GCS bucket for PDF relationship reports; defaults to '<project_id>-graph-reports' when empty"
}

variable "vertex_ai_location" {
  type        = string
  default     = "us-central1"
  description = "Region for Vertex AI Gemini calls used to generate node aliases"
}

variable "scheduler_cron" {
  type        = string
  default     = "0 7 * * *"
  description = "Cloud Scheduler cron expression for the daily schema-check run (default: 7am daily)"
}

variable "scheduler_timezone" {
  type        = string
  default     = "America/Chicago"
  description = "IANA time zone the scheduler_cron expression is evaluated in"
}

variable "cloudbuild_repo_uri" {
  type        = string
  description = "HTTPS URI of the Git repo (GitHub/GitLab/Cloud Source Repositories) containing this Terraform config and cloudbuild.yaml, used by the Cloud Build trigger"
}
