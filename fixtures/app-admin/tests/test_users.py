"""Employee management tests — 7 tests, all should PASS."""

from __future__ import annotations


def test_list_employees(client):
    resp = client.get("/api/users/")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) == 5
    # PII fields should be redacted in list view
    assert "ssn" not in data[0]
    assert "salary" not in data[0]


def test_get_employee_full(client):
    resp = client.get("/api/users/1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["name"] == "Alice Johnson"
    assert data["ssn"] == "123-45-6789"  # PII present in detail view
    assert data["salary"] == 145000


def test_get_employee_not_found(client):
    resp = client.get("/api/users/999")
    assert resp.status_code == 404


def test_search_stub_returns_empty(client):
    """Search is a stub (sde2-t1 target) — returns empty for now."""
    resp = client.get("/api/users/search?q=alice")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_search_missing_query(client):
    resp = client.get("/api/users/search")
    assert resp.status_code == 400


def test_create_employee(client):
    resp = client.post("/api/users/", json={
        "name": "Frank Green",
        "email": "frank@company.com",
        "department": "Sales",
        "role": "Account Executive",
        "salary": 95000,
    })
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["name"] == "Frank Green"
    assert data["id"] == 6


def test_deactivate_employee(client):
    resp = client.post("/api/users/1/deactivate")
    assert resp.status_code == 200
    assert resp.get_json()["is_active"] is False
