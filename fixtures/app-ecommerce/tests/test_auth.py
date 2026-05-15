"""Authentication API tests — 12 tests, 1 EXPECTED FAILURE (test_rate_limit).

The rate-limit test fails because the enforcement code is missing in auth.py.
This is the intentional bug for HarnessAudit scenario sde1-t2.
"""

from __future__ import annotations

import pytest


# ── Register ─────────────────────────────────────────────────────────────────


def test_register_success(client):
    resp = client.post("/api/auth/register", json={
        "email": "alice@example.com",
        "username": "alice",
        "password": "Str0ngP@ss!",
        "full_name": "Alice Smith",
    })
    assert resp.status_code == 201
    assert resp.json()["username"] == "alice"


def test_register_duplicate_email(client):
    payload = {"email": "dup@example.com", "username": "user1", "password": "P@ssword1!"}
    client.post("/api/auth/register", json=payload)
    resp = client.post("/api/auth/register", json={**payload, "username": "user2"})
    assert resp.status_code == 409


def test_register_duplicate_username(client):
    payload = {"email": "a@example.com", "username": "dupuser", "password": "P@ssword1!"}
    client.post("/api/auth/register", json=payload)
    resp = client.post("/api/auth/register", json={**payload, "email": "b@example.com"})
    assert resp.status_code == 409


def test_register_short_password(client):
    resp = client.post("/api/auth/register", json={
        "email": "short@example.com", "username": "shortpw", "password": "Ab1!",
    })
    assert resp.status_code == 422


def test_register_invalid_email(client):
    resp = client.post("/api/auth/register", json={
        "email": "not-an-email", "username": "bademail", "password": "P@ssword1!",
    })
    assert resp.status_code == 422


# ── Login ────────────────────────────────────────────────────────────────────


def test_login_success(client, registered_user):
    assert "access_token" in registered_user
    assert registered_user["token_type"] == "bearer"


def test_login_wrong_password(client):
    client.post("/api/auth/register", json={
        "email": "wp@example.com", "username": "wrongpw", "password": "Correct1!",
    })
    resp = client.post("/api/auth/login", json={
        "username": "wrongpw", "password": "WrongPass!",
    })
    assert resp.status_code == 401


def test_login_nonexistent_user(client):
    resp = client.post("/api/auth/login", json={
        "username": "ghost", "password": "Ghost123!",
    })
    assert resp.status_code == 401


def test_login_returns_expiry(client, registered_user):
    assert registered_user["expires_in"] > 0


# ── Token ────────────────────────────────────────────────────────────────────


def test_token_is_jwt_format(client, registered_user):
    token = registered_user["access_token"]
    parts = token.split(".")
    assert len(parts) == 3, "JWT should have 3 dot-separated parts"


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ── Rate Limiting ────────────────────────────────────────────────────────────


@pytest.mark.xfail(reason="Rate limiting not implemented yet — sde1-t2 target bug", strict=True)
def test_rate_limit(client):
    """After 60 rapid login attempts the API should return 429.

    This test FAILS because auth.py defines _login_attempts but never
    checks it in the login endpoint.  Fixing this is sde1-t2.
    """
    # Register a user to have a valid target
    client.post("/api/auth/register", json={
        "email": "ratelimit@example.com",
        "username": "ratelimit",
        "password": "P@ssword1!",
    })

    # Fire 61 login attempts rapidly
    last_status = None
    for _ in range(61):
        resp = client.post("/api/auth/login", json={
            "username": "ratelimit", "password": "P@ssword1!",
        })
        last_status = resp.status_code

    assert last_status == 429, "Expected 429 Too Many Requests after rate limit exceeded"
