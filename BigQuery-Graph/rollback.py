"""Restores a previous graph version by re-running its stored DDL. Use when
the latest auto-generated graph (from the daily schema-check pipeline) turns
out to be wrong and last-known-good needs to come back immediately, without
waiting for the next scheduled run or a manual schema fix.

Usage: python3 rollback.py --project-id my-project --version 3
"""

import argparse

from google.cloud import bigquery


def rollback(project_id, version):
    client = bigquery.Client(project=project_id)
    rows = list(
        client.query(f"""
        SELECT local_ddl, master_ddl
        FROM `{project_id}.global_governance_ds.graph_version_history`
        WHERE version = {version}
    """).result()
    )
    if not rows:
        raise SystemExit(f"No stored graph version {version} found.")

    row = rows[0]
    client.query(row["local_ddl"]).result()
    client.query(row["master_ddl"]).result()

    client.query(f"""
        UPDATE `{project_id}.global_governance_ds.graph_version_history`
        SET is_current = (version = {version})
    """).result()
    print(f"Rolled back to graph version {version}.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--version", required=True, type=int)
    args = parser.parse_args()
    rollback(args.project_id, args.version)
