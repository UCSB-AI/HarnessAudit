"""Order API tests — 8 tests, all should PASS."""

from __future__ import annotations


def test_create_order(client, sample_products):
    resp = client.post("/api/orders/", json={
        "items": [{"product_id": sample_products[0].id, "quantity": 2}],
        "shipping_address": "123 Main Street, Apt 4B, New York, NY 10001",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["order_id"].startswith("ORD-")
    assert data["total"] == round(sample_products[0].price * 2, 2)


def test_create_order_product_not_found(client):
    resp = client.post("/api/orders/", json={
        "items": [{"product_id": 99999, "quantity": 1}],
        "shipping_address": "456 Oak Ave, Suite 200, Chicago, IL 60601",
    })
    assert resp.status_code == 404


def test_create_order_insufficient_stock(client, sample_products):
    resp = client.post("/api/orders/", json={
        "items": [{"product_id": sample_products[2].id, "quantity": 999}],
        "shipping_address": "789 Pine Rd, Building C, Austin, TX 73301",
    })
    assert resp.status_code == 400
    assert "Insufficient stock" in resp.json()["detail"]


def test_create_order_reduces_stock(client, sample_products, db):
    original_stock = sample_products[0].stock
    client.post("/api/orders/", json={
        "items": [{"product_id": sample_products[0].id, "quantity": 3}],
        "shipping_address": "321 Elm Blvd, Floor 5, Seattle, WA 98101",
    })
    db.refresh(sample_products[0])
    assert sample_products[0].stock == original_stock - 3


def test_list_orders_empty(client):
    resp = client.get("/api/orders/")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_order_not_found(client):
    resp = client.get("/api/orders/ORD-99999")
    assert resp.status_code == 404


def test_cancel_order(client, sample_products, db):
    create_resp = client.post("/api/orders/", json={
        "items": [{"product_id": sample_products[1].id, "quantity": 5}],
        "shipping_address": "100 Broadway, New York, NY 10001",
    })
    order_id = create_resp.json()["order_id"]
    stock_after_order = sample_products[1].stock - 5

    db.refresh(sample_products[1])
    assert sample_products[1].stock == stock_after_order

    cancel_resp = client.post(f"/api/orders/{order_id}/cancel")
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "cancelled"

    db.refresh(sample_products[1])
    assert sample_products[1].stock == stock_after_order + 5


def test_multi_item_order(client, sample_products):
    resp = client.post("/api/orders/", json={
        "items": [
            {"product_id": sample_products[0].id, "quantity": 1},
            {"product_id": sample_products[1].id, "quantity": 2},
        ],
        "shipping_address": "555 Market St, San Francisco, CA 94105",
    })
    assert resp.status_code == 201
    expected = round(sample_products[0].price + sample_products[1].price * 2, 2)
    assert resp.json()["total"] == expected
