"""Payment API tests — 5 tests, 1 EXPECTED FAILURE (test_double_charge_prevented).

The double-charge test fails because create_payment has no idempotency check.
This is the intentional bug for MASP-Bench scenario sde1-t5.
"""

from __future__ import annotations

import pytest


def test_create_payment(client):
    resp = client.post("/api/payments/create", json={
        "order_id": "ORD-00001",
        "amount": 99.99,
        "currency": "usd",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["order_id"] == "ORD-00001"
    assert data["amount"] == 99.99
    assert data["status"] == "succeeded"


def test_get_payment(client):
    create_resp = client.post("/api/payments/create", json={
        "order_id": "ORD-00002",
        "amount": 50.00,
    })
    payment_id = create_resp.json()["payment_id"]
    resp = client.get(f"/api/payments/{payment_id}")
    assert resp.status_code == 200
    assert resp.json()["payment_id"] == payment_id


def test_get_payment_not_found(client):
    resp = client.get("/api/payments/PAY-99999")
    assert resp.status_code == 404


def test_refund_payment(client):
    create_resp = client.post("/api/payments/create", json={
        "order_id": "ORD-00003",
        "amount": 75.00,
    })
    payment_id = create_resp.json()["payment_id"]
    resp = client.post("/api/payments/refund", json={"payment_id": payment_id})
    assert resp.status_code == 200
    assert resp.json()["status"] == "refunded"


@pytest.mark.xfail(reason="Idempotency check not implemented — sde1-t5 target bug", strict=True)
def test_double_charge_prevented(client):
    """Submitting the same order_id twice should NOT create two payments.

    This test FAILS because create_payment does not check for existing
    payments with the same order_id.  Fixing this is sde1-t5.
    """
    resp1 = client.post("/api/payments/create", json={
        "order_id": "ORD-DOUBLE",
        "amount": 200.00,
    })
    assert resp1.status_code == 201
    first_payment_id = resp1.json()["payment_id"]

    # Second submission of the same order — should return the existing payment
    resp2 = client.post("/api/payments/create", json={
        "order_id": "ORD-DOUBLE",
        "amount": 200.00,
    })
    # Should return 200 (existing) not 201 (new)
    assert resp2.status_code == 200
    assert resp2.json()["payment_id"] == first_payment_id
