"""Shared fixtures for the e-commerce test suite."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, StaticPool
from sqlalchemy.orm import sessionmaker

from src.db.connection import Base, get_db
from src.main import app
from src.models.product import Product
from src.models.user import User  # noqa: F401 — ensure table is created

# In-memory SQLite for tests
_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSession = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


def _override_get_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db


@pytest.fixture(autouse=True)
def setup_db():
    """Create fresh tables before each test, drop after. Reset in-memory stores."""
    from src.api import orders
    Base.metadata.create_all(bind=_engine)
    # Reset in-memory state
    orders._orders.clear()
    orders._order_counter = 0
    yield
    Base.metadata.drop_all(bind=_engine)


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def db():
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def sample_products(db):
    """Seed 5 products for testing."""
    products = [
        Product(name="Laptop", description="A fast laptop", price=999.99, stock=10, category="electronics"),
        Product(name="Keyboard", description="Mechanical keyboard", price=79.99, stock=50, category="electronics"),
        Product(name="Desk", description="Standing desk", price=349.00, stock=5, category="furniture"),
        Product(name="Mouse", description="Wireless mouse", price=29.99, stock=100, category="electronics"),
        Product(name="Monitor", description="4K display", price=449.00, stock=8, category="electronics"),
    ]
    db.add_all(products)
    db.commit()
    for p in products:
        db.refresh(p)
    return products


@pytest.fixture()
def registered_user(client):
    """Register and return a test user with auth token."""
    resp = client.post("/api/auth/register", json={
        "email": "test@example.com",
        "username": "testuser",
        "password": "SecureP@ss123",
        "full_name": "Test User",
    })
    assert resp.status_code == 201
    login_resp = client.post("/api/auth/login", json={
        "username": "testuser",
        "password": "SecureP@ss123",
    })
    assert login_resp.status_code == 200
    return login_resp.json()
