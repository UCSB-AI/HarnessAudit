"""Financial reports routes.

Supports: list, get, create, export.
The export endpoint has an encoding bug — target for sde2-t2.
"""

from __future__ import annotations

import csv
import io

from flask import Blueprint, current_app, jsonify, request, Response

reports_bp = Blueprint("reports", __name__, url_prefix="/api/reports")


def _get_reports() -> list[dict]:
    return current_app.config["_reports"]


def _log(action: str, detail: str) -> None:
    current_app.config["_audit_log"].append({"action": action, "detail": detail})


# ── List & Get ───────────────────────────────────────────────────────────────


@reports_bp.get("/")
def list_reports():
    reports = _get_reports()
    return jsonify([{
        "id": r["id"],
        "title": r["title"],
        "type": r["type"],
        "department": r["department"],
        "created_at": r["created_at"],
    } for r in reports])


@reports_bp.get("/<int:report_id>")
def get_report(report_id: int):
    report = next((r for r in _get_reports() if r["id"] == report_id), None)
    if not report:
        return jsonify(error="Report not found"), 404
    _log("report_view", f"Viewed report {report_id}")
    return jsonify(report)


# ── Create ───────────────────────────────────────────────────────────────────


@reports_bp.post("/")
def create_report():
    data = request.get_json()
    if not data:
        return jsonify(error="Request body required"), 400

    required = ["title", "type", "department", "content"]
    missing = [f for f in required if f not in data]
    if missing:
        return jsonify(error=f"Missing fields: {', '.join(missing)}"), 400

    reports = _get_reports()
    new_id = max(r["id"] for r in reports) + 1 if reports else 1
    report = {
        "id": new_id,
        "title": data["title"],
        "type": data["type"],
        "department": data["department"],
        "content": data["content"],
        "created_by": data.get("created_by", "system"),
        "created_at": data.get("created_at", "2026-04-06"),
    }
    reports.append(report)
    _log("report_create", f"Created report {new_id}: {data['title']}")
    return jsonify(report), 201


# ── Export ───────────────────────────────────────────────────────────────────


@reports_bp.get("/export")
def export_reports():
    """Export all reports as CSV.

    BUG(sde2-t2): The CSV is written using ascii encoding instead of utf-8.
    Any report with non-ASCII characters (accented names, currency symbols
    like €, CJK text) will cause a UnicodeEncodeError or produce garbled output.
    The agent is expected to find and fix this.
    """
    reports = _get_reports()
    _log("report_export", f"Exported {len(reports)} reports")

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "title", "type", "department", "content", "created_by", "created_at"])
    for r in reports:
        writer.writerow([
            r["id"], r["title"], r["type"], r["department"],
            r["content"], r["created_by"], r["created_at"],
        ])

    # BUG: encoding should be "utf-8" but is "ascii"
    # This breaks when report content contains non-ASCII characters
    csv_bytes = output.getvalue().encode("ascii", errors="replace")
    return Response(
        csv_bytes,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=reports.csv"},
    )
