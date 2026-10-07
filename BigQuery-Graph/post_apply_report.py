"""Runs after `terraform apply` inside the daily Cloud Build pipeline
(cloudbuild.yaml). Reads this run's Terraform outputs -- schema_hash /
schema_changed / relationship_report_json, all emitted by generate_graphs.py's
external data source -- and, only when the schema actually changed, does the
work that shouldn't live inside a Terraform data source (data sources must
stay side-effect-free/idempotent across `plan`, but this is a one-shot
action per real schema change): builds the PDF report, uploads it to GCS,
asks Vertex AI for node aliases, records a new row in
graph_version_history (preserving every prior version's DDL for rollback via
rollback.py), updates graph_schema_state, and emails the developer.
"""

import json
import os
import smtplib
import subprocess
from datetime import UTC, datetime
from email.message import EmailMessage

from alias_generator import generate_aliases_for_dataset, write_aliases
from google.cloud import bigquery, storage
from pdf_report import build_report


def _terraform_output():
    raw = subprocess.check_output(["terraform", "output", "-json"], text=True)
    return json.loads(raw)


def _next_version(client, project_id):
    query = f"""
        SELECT MAX(version) AS max_version
        FROM `{project_id}.global_governance_ds.graph_version_history`
    """
    rows = list(client.query(query).result())
    current = rows[0]["max_version"] if rows and rows[0]["max_version"] is not None else 0
    return current + 1


def _send_alert(alert_email, project_id, version, pdf_uri):
    smtp_host = os.environ["SMTP_HOST"]
    smtp_user = os.environ["SMTP_USER"]
    smtp_password = os.environ["SMTP_PASSWORD"]

    msg = EmailMessage()
    msg["Subject"] = f"[{project_id}] New BigQuery graph version {version} generated"
    msg["From"] = smtp_user
    msg["To"] = alert_email
    msg.set_content(
        f"A schema change was detected in project {project_id}.\n\n"
        f"New graph version: {version}\n"
        f"Relationship report: {pdf_uri}\n\n"
        f"The previous graph version has been preserved in "
        f"global_governance_ds.graph_version_history and can be restored "
        f"with `python3 rollback.py --project-id {project_id} --version <N>` "
        f"if version {version} causes issues."
    )

    with smtplib.SMTP_SSL(smtp_host, 465) as server:
        server.login(smtp_user, smtp_password)
        server.send_message(msg)


def main():
    outputs = _terraform_output()
    schema_changed = outputs["schema_changed"]["value"] == "true"
    if not schema_changed:
        print("No schema changes detected; skipping report/alias/alert generation.")
        return

    project_id = outputs["project_id"]["value"]
    schema_hash = outputs["schema_hash"]["value"]
    # One deploy job (and one DDL string) per dataset location -- see the
    # comment on google_bigquery_job.deploy_local_graphs in
    # graph_automation.tf for why a single job can't span locations. Joined
    # here purely for archival in graph_version_history; rollback.py replays
    # each location's DDL separately.
    local_ddl = "\n".join(outputs["local_ddl_by_location"]["value"].values())
    master_ddl = outputs["master_ddl_query"]["value"]
    relationship_report = json.loads(outputs["relationship_report_json"]["value"])

    alert_email = os.environ["ALERT_EMAIL"]
    bucket_name = os.environ["REPORT_BUCKET"]
    vertex_location = os.environ.get("VERTEX_AI_LOCATION", "us-central1")

    bq = bigquery.Client(project=project_id)
    version = _next_version(bq, project_id)

    pdf_path = f"/tmp/graph_report_v{version}.pdf"
    build_report(project_id, relationship_report, pdf_path)

    gcs = storage.Client(project=project_id)
    blob = gcs.bucket(bucket_name).blob(f"reports/v{version}_{schema_hash[:12]}.pdf")
    blob.upload_from_filename(pdf_path)
    pdf_uri = f"gs://{bucket_name}/{blob.name}"

    for entry in relationship_report:
        tables = {t["table"]: t["columns"] for t in entry["tables"]}
        alias_rows = generate_aliases_for_dataset(
            project_id, entry["dataset"], tables, vertex_location
        )
        write_aliases(bq, project_id, alias_rows)

    now = datetime.now(UTC).isoformat()
    bq.query(f"""
        UPDATE `{project_id}.global_governance_ds.graph_version_history`
        SET is_current = FALSE
        WHERE is_current = TRUE
    """).result()
    bq.insert_rows_json(
        f"{project_id}.global_governance_ds.graph_version_history",
        [
            {
                "version": version,
                "created_at": now,
                "schema_hash": schema_hash,
                "local_ddl": local_ddl,
                "master_ddl": master_ddl,
                "pdf_report_uri": pdf_uri,
                "is_current": True,
            }
        ],
    )
    bq.insert_rows_json(
        f"{project_id}.global_governance_ds.graph_schema_state",
        [
            {
                "schema_hash": schema_hash,
                "checked_at": now,
                "schema_changed": True,
            }
        ],
    )

    _send_alert(alert_email, project_id, version, pdf_uri)
    print(f"Graph version {version} generated and alert sent to {alert_email}.")


if __name__ == "__main__":
    main()
