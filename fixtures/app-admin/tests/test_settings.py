"""System settings tests — 3 tests, all should PASS."""

from __future__ import annotations


def test_get_settings(client):
    resp = client.get("/api/settings/")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["maintenance_mode"] is False
    assert data["backup_enabled"] is True


def test_update_settings(client):
    resp = client.put("/api/settings/", json={
        "maintenance_mode": True,
        "log_level": "DEBUG",
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["maintenance_mode"] is True
    assert data["log_level"] == "DEBUG"


def test_reset_settings(client):
    # Change something first
    client.put("/api/settings/", json={"maintenance_mode": True})
    # Then reset
    resp = client.post("/api/settings/reset")
    assert resp.status_code == 200
    # Verify reset
    check = client.get("/api/settings/")
    assert check.get_json()["maintenance_mode"] is False
