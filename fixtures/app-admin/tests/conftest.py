"""Shared fixtures for the admin dashboard test suite."""

from __future__ import annotations

import pytest

from src.app import create_app


@pytest.fixture()
def app():
    app = create_app(testing=True)
    yield app


@pytest.fixture()
def client(app):
    return app.test_client()
