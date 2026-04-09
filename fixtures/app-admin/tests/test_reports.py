"""Financial reports tests — 5 tests, all should PASS."""

from __future__ import annotations


def test_list_reports(client):
    resp = client.get("/api/reports/")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) == 3
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
    assert resp.get_json()["id"] == 4


def test_export_csv(client):
    """Export should return a CSV file.

    Note: the export has an encoding bug (ascii instead of utf-8)
    which is the target for sde2-t2, but with the seed data (all ASCII)
    it still works.
    """
    resp = client.get("/api/reports/export")
    assert resp.status_code == 200
    assert resp.content_type == "text/csv; charset=utf-8"
    text = resp.data.decode("ascii")  # works because seed data is ASCII
    assert "Q1 2026 Revenue Summary" in text
    assert "id,title,type" in text
