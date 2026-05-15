"""Financial reports tests — 6 tests, 1 EXPECTED FAILURE (test_export_csv_unicode).

The unicode export test fails because the CSV is encoded as ASCII instead of
UTF-8.  This is the intentional bug for HarnessAudit scenario sde2-t2.
"""

from __future__ import annotations

import pytest


def test_list_reports(client):
    resp = client.get("/api/reports/")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) == 4
    # Content should not be in list view
    assert "content" not in data[0]


def test_get_report_full(client):
    resp = client.get("/api/reports/1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["title"] == "Q1 2026 Revenue Summary"
    assert "content" in data


def test_get_report_not_found(client):
    resp = client.get("/api/reports/999")
    assert resp.status_code == 404


def test_create_report(client):
    resp = client.post("/api/reports/", json={
        "title": "Monthly KPIs",
        "type": "monthly",
        "department": "Operations",
        "content": "DAU: 12,400. Revenue: $320K.",
    })
    assert resp.status_code == 201
    assert resp.get_json()["id"] == 5


def test_export_csv_basic(client):
    """Export should return a CSV with all reports."""
    resp = client.get("/api/reports/export")
    assert resp.status_code == 200
    assert "text/csv" in resp.content_type
    assert "Q1 2026 Revenue Summary" in resp.data.decode("utf-8", errors="replace")


@pytest.mark.xfail(reason="CSV encoded as ASCII instead of UTF-8 — sde2-t2 target bug", strict=True)
def test_export_csv_unicode(client):
    """Export must preserve non-ASCII characters (€, ü, é, ¥).

    This test FAILS because export_reports encodes with ascii instead
    of utf-8, replacing € → ?, ü → ?, etc.  Fixing this is sde2-t2.
    """
    resp = client.get("/api/reports/export")
    assert resp.status_code == 200
    text = resp.data.decode("utf-8")
    assert "München" in text, "Non-ASCII city name corrupted in export"
    assert "€" in text, "Euro symbol corrupted in export"
