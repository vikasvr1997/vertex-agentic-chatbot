"""Builds a human-readable PDF explaining, for every dataset, how each
table/node connects to other tables via foreign keys -- consumes the
relationship_report payload produced by generate_graphs.py's per-dataset
graph builder."""

from datetime import UTC, datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def build_report(project_id, relationship_report, output_path):
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(output_path, pagesize=LETTER)
    story = [
        Paragraph("BigQuery Property Graph Relationship Report", styles["Title"]),
        Paragraph(f"Project: {project_id}", styles["Normal"]),
        Paragraph(f"Generated: {datetime.now(UTC).isoformat()}", styles["Normal"]),
        Spacer(1, 0.3 * inch),
    ]

    for entry in relationship_report:
        story.append(Paragraph(f"Dataset: {entry['dataset']}", styles["Heading1"]))

        for t in entry["tables"]:
            story.append(
                Paragraph(
                    f"Table <b>{t['table']}</b> — primary key: {t['primary_key'] or 'none detected'}",
                    styles["Heading3"],
                )
            )
            story.append(Paragraph(", ".join(t["columns"]), styles["Normal"]))
            story.append(Spacer(1, 0.1 * inch))

        if entry["relationships"]:
            story.append(Paragraph("Relationships", styles["Heading2"]))
            data = [["From table", "FK column", "To table", "To key"]]
            for r in entry["relationships"]:
                data.append([r["from_table"], r["fk_column"], r["to_table"], r["to_key"]])
            tbl = Table(data, hAlign="LEFT")
            tbl.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#333333")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                        ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ]
                )
            )
            story.append(tbl)
        else:
            story.append(Paragraph("No foreign-key relationships detected.", styles["Normal"]))

        story.append(Spacer(1, 0.3 * inch))

    doc.build(story)
    return output_path
