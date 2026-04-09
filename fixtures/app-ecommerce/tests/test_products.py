"""Product API tests — 18 tests, all should PASS."""

from __future__ import annotations


# ── List / Read ──────────────────────────────────────────────────────────────


def test_list_products_empty(client):
    resp = client.get("/api/products/")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_products_returns_seeded(client, sample_products):
    resp = client.get("/api/products/")
    assert resp.status_code == 200
    assert len(resp.json()) == 5


def test_list_products_filter_by_category(client, sample_products):
    resp = client.get("/api/products/?category=furniture")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "Desk"


def test_list_products_pagination_params_accepted(client, sample_products):
    resp = client.get("/api/products/?page=1&per_page=2")
    assert resp.status_code == 200
    # NOTE: pagination not actually implemented yet (sde1-t1), so all returned
    assert isinstance(resp.json(), list)


def test_get_product_by_id(client, sample_products):
    resp = client.get(f"/api/products/{sample_products[0].id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Laptop"


def test_get_product_not_found(client):
    resp = client.get("/api/products/9999")
    assert resp.status_code == 404


# ── Create ───────────────────────────────────────────────────────────────────


def test_create_product(client):
    resp = client.post("/api/products/", json={
        "name": "Headphones", "price": 59.99, "stock": 20, "category": "audio",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Headphones"
    assert data["price"] == 59.99


def test_create_product_missing_name(client):
    resp = client.post("/api/products/", json={"price": 10.0})
    assert resp.status_code == 422


def test_create_product_negative_price(client):
    resp = client.post("/api/products/", json={"name": "Bad", "price": -5})
    assert resp.status_code == 422


def test_create_product_zero_stock_default(client):
    resp = client.post("/api/products/", json={"name": "Widget", "price": 1.00})
    assert resp.status_code == 201
    assert resp.json()["stock"] == 0


def test_create_product_with_description_html(client):
    resp = client.post("/api/products/", json={
        "name": "Test", "price": 5.0, "description": "Hello world",
    })
    assert resp.status_code == 201
    assert "description_html" in resp.json()


# ── Update ───────────────────────────────────────────────────────────────────


def test_update_product_name(client, sample_products):
    pid = sample_products[0].id
    resp = client.put(f"/api/products/{pid}", json={"name": "Gaming Laptop"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Gaming Laptop"


def test_update_product_price(client, sample_products):
    pid = sample_products[1].id
    resp = client.put(f"/api/products/{pid}", json={"price": 89.99})
    assert resp.status_code == 200
    assert resp.json()["price"] == 89.99


def test_update_product_not_found(client):
    resp = client.put("/api/products/9999", json={"name": "Ghost"})
    assert resp.status_code == 404


def test_update_partial_fields(client, sample_products):
    pid = sample_products[2].id
    resp = client.put(f"/api/products/{pid}", json={"stock": 99})
    assert resp.status_code == 200
    assert resp.json()["stock"] == 99
    assert resp.json()["name"] == "Desk"  # unchanged


# ── Delete ───────────────────────────────────────────────────────────────────


def test_delete_product(client, sample_products):
    pid = sample_products[0].id
    resp = client.delete(f"/api/products/{pid}")
    assert resp.status_code == 204
    assert client.get(f"/api/products/{pid}").status_code == 404


def test_delete_product_not_found(client):
    resp = client.delete("/api/products/9999")
    assert resp.status_code == 404
