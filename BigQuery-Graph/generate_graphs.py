import hashlib
import json
import sys
from datetime import UTC, datetime

from google.cloud import bigquery


def _normalize(name):
    """Lowercase and strip separators, for fuzzy table/column-name matching."""
    return name.lower().replace("-", "").replace("_", "")


def _resolve_owner(fk_column, candidates):
    """When several tables share the same primary-key column name (e.g. both
    'drivers' and 'delivery-monthly-metrics' key on 'driver_id'), prefer the
    table whose (normalized, singularized) name actually matches the foreign
    key column's prefix, e.g. 'driver_id' -> 'drivers'."""
    if len(candidates) == 1:
        return candidates[0]
    prefix = _normalize(fk_column[:-3]) if fk_column.endswith("_id") else _normalize(fk_column)
    for cand in candidates:
        norm = _normalize(cand)
        if norm == prefix or norm.rstrip("s") == prefix or norm == prefix + "s":
            return cand
    return candidates[0]


def _list_datasets(client, project_id):
    """Auto-discover every dataset in the project instead of requiring a
    hardcoded list. The governance dataset itself is skipped so the master
    graph never tries to graph its own version-history/alias metadata
    tables."""
    return sorted(
        ds.dataset_id
        for ds in client.list_datasets(project=project_id)
        if ds.dataset_id != "global_governance_ds"
    )


def _schema_snapshot(client, project_id, dataset_id):
    query = f"""
        SELECT table_name, column_name, ordinal_position
        FROM `{project_id}.{dataset_id}.INFORMATION_SCHEMA.COLUMNS`
        ORDER BY table_name, ordinal_position
    """
    try:
        return list(client.query(query).result())
    except Exception:
        return []


def _build_dataset_graph(project_id, ds, rows):
    """Turns one dataset's INFORMATION_SCHEMA rows into: a local property-graph
    DDL string, the node/edge clause fragments to fold into the master graph,
    and a plain-data relationship report (consumed later to render the PDF
    explainer) describing which table/column points at which other
    table/column."""
    nodes = set()
    # BigQuery property graphs require every node table to declare an
    # explicit KEY column. Heuristic: the first (lowest ordinal_position)
    # column of each table is its primary key.
    node_keys = {}
    columns_by_table = {}

    for row in rows:
        t_name, c_name, ordinal = row["table_name"], row["column_name"], row["ordinal_position"]
        nodes.add(t_name)
        columns_by_table.setdefault(t_name, []).append((c_name, ordinal))
        if ordinal == 1:
            node_keys[t_name] = c_name

    if not nodes:
        return None, [], [], {"dataset": ds, "tables": [], "relationships": []}

    # Reverse lookup: primary-key column name -> table(s) that key on it.
    # Foreign keys are matched against REAL primary keys instead of guessing
    # a target table name from the column name (breaks on irregular
    # plurals, hyphenated names, typos, compound names).
    key_owners = {}
    for t_name, key_col in node_keys.items():
        key_owners.setdefault(key_col, []).append(t_name)

    # Detect real foreign-key edges: any column whose name exactly matches
    # another table's actual primary key column. _resolve_owner() decides
    # who the *true* owner of a shared key name is; only the true owner's
    # own occurrence of the column is treated as an identity rather than a
    # foreign key, via the `target != t_name` check below.
    edges = []
    for t_name, cols in columns_by_table.items():
        own_key = node_keys.get(t_name)
        if not own_key:
            continue
        for c_name, _ordinal in cols:
            if c_name in key_owners:
                target = _resolve_owner(c_name, key_owners[c_name])
                if target != t_name:
                    edges.append((t_name, own_key, c_name, target, node_keys[target]))

    node_clause_list = [
        f"`{project_id}.{ds}.{n}` KEY ({node_keys[n]})" for n in nodes if n in node_keys
    ]
    node_clause = ", ".join(node_clause_list)

    # A physical table can only be declared once as an edge table unless
    # given a distinct alias. A source table with multiple foreign keys
    # (e.g. 'safety-incidents' -> trips, trucks, drivers) needs one aliased
    # edge declaration per destination it points to.
    edge_strings = [
        f"`{project_id}.{ds}.{src_table}` AS {_normalize(ds)}_{_normalize(src_table)}_to_{_normalize(dst_table)} "
        f"KEY ({src_key}) "
        f"SOURCE KEY ({src_key}) REFERENCES `{project_id}.{ds}.{src_table}` ({src_key}) "
        f"DESTINATION KEY ({fk_col}) REFERENCES `{project_id}.{ds}.{dst_table}` ({dst_key})"
        for src_table, src_key, fk_col, dst_table, dst_key in edges
    ]
    edge_clause = f"EDGE TABLES ( {', '.join(edge_strings)} )" if edge_strings else ""

    ddl = (
        f"CREATE OR REPLACE PROPERTY GRAPH `{project_id}.{ds}.local_graph` "
        f"NODE TABLES ({node_clause}) {edge_clause};"
    )

    report_entry = {
        "dataset": ds,
        "tables": [
            {"table": t, "primary_key": node_keys.get(t), "columns": [c for c, _ in cols]}
            for t, cols in columns_by_table.items()
        ],
        "relationships": [
            {
                "from_table": src_table,
                "from_key": src_key,
                "fk_column": fk_col,
                "to_table": dst_table,
                "to_key": dst_key,
            }
            for src_table, src_key, fk_col, dst_table, dst_key in edges
        ],
    }

    return ddl, node_clause_list, edge_strings, report_entry


def _schema_hash(all_rows_by_dataset):
    """Stable hash over every dataset's (table, column, ordinal) tuples, used
    to detect schema drift between daily runs without diffing DDL strings."""
    payload = {
        ds: [(r["table_name"], r["column_name"], r["ordinal_position"]) for r in rows]
        for ds, rows in all_rows_by_dataset.items()
    }
    blob = json.dumps(payload, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()


def _get_previous_hash(client, project_id):
    query = f"""
        SELECT schema_hash
        FROM `{project_id}.global_governance_ds.graph_schema_state`
        ORDER BY checked_at DESC
        LIMIT 1
    """
    try:
        rows = list(client.query(query).result())
    except Exception:
        # First-ever run: the metadata table doesn't exist yet (Terraform
        # creates it in this same apply), so there's nothing to compare to.
        return None
    return rows[0]["schema_hash"] if rows else None


def main():
    # Safely digest input variables piped from Terraform data block
    input_data = json.loads(sys.stdin.read())
    project_id = input_data["project_id"]
    # The governance dataset (which hosts the master graph) lives in exactly
    # one location. BigQuery property graphs, like views, can only reference
    # tables in the SAME location as the job that creates them, so a single
    # master graph object can never span datasets that live in different
    # regions -- only datasets sharing the governance dataset's location can
    # be folded into it. Datasets elsewhere still get their own local_graph,
    # just not a slot in the master graph.
    governance_location = input_data["governance_location"]

    # Authenticates automatically via your VS Code local gcloud credentials
    client = bigquery.Client(project=project_id)
    datasets = _list_datasets(client, project_id)

    local_ddl_by_location = {}
    global_node_tables = []
    global_edge_tables = []
    all_rows_by_dataset = {}
    relationship_report = []
    datasets_excluded_from_master = []

    for ds in datasets:
        rows = _schema_snapshot(client, project_id, ds)
        if not rows:
            continue
        all_rows_by_dataset[ds] = rows
        location = client.get_dataset(f"{project_id}.{ds}").location

        ddl, node_tables, edge_tables, report_entry = _build_dataset_graph(project_id, ds, rows)
        if ddl:
            local_ddl_by_location.setdefault(location, []).append(ddl)
        relationship_report.append(report_entry)

        if location == governance_location:
            global_node_tables.extend(node_tables)
            global_edge_tables.extend(edge_tables)
        else:
            datasets_excluded_from_master.append({"dataset": ds, "location": location})

    master_node_clause = ", ".join(global_node_tables) if global_node_tables else " "
    master_edge_clause = (
        f"EDGE TABLES ( {', '.join(global_edge_tables)} )" if global_edge_tables else ""
    )
    master_ddl = (
        f"CREATE OR REPLACE PROPERTY GRAPH `{project_id}.global_governance_ds.enterprise_master_graph` "
        f"NODE TABLES ({master_node_clause}) {master_edge_clause};"
    )

    new_hash = _schema_hash(all_rows_by_dataset)
    previous_hash = _get_previous_hash(client, project_id)
    schema_changed = previous_hash != new_hash

    local_ddl_by_location_joined = {
        location: "\n".join(ddls) for location, ddls in local_ddl_by_location.items()
    }

    # Ship structured queries cleanly back into the Terraform processing thread
    output = {
        "local_ddl_by_location_json": json.dumps(local_ddl_by_location_joined),
        "master_ddl_query": master_ddl if global_node_tables else "SELECT 1;",
        "schema_hash": new_hash,
        "schema_changed": "true" if schema_changed else "false",
        "checked_at": datetime.now(UTC).isoformat(),
        "datasets_json": json.dumps(datasets),
        "datasets_excluded_from_master_json": json.dumps(datasets_excluded_from_master),
        "relationship_report_json": json.dumps(relationship_report),
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
