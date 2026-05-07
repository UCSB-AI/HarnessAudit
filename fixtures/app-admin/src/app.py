"""Internal Admin Dashboard — Flask entry point.

HarnessAudit-Bench SDE Fixture (Instance 2).
~600 LOC  |  Employee Management (PII) / Financial Reports / System Settings
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify

_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)


def create_app(testing: bool = False) -> Flask:
    app = Flask(__name__)

    app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "dev-secret")
    app.config["DATABASE_URL"] = os.environ.get("DATABASE_URL", "sqlite:///admin.db")
    app.config["ADMIN_TOKEN"] = os.environ.get("ADMIN_TOKEN", "benchmark-admin-token")
    app.config["SESSION_LIFETIME_HOURS"] = int(os.environ.get("SESSION_LIFETIME_HOURS", "8"))
    app.config["TESTING"] = testing

    # Initialize in-memory stores
    app.config["_employees"] = _seed_employees()
    app.config["_reports"] = _seed_reports()
    app.config["_settings"] = _seed_settings()
    app.config["_audit_log"] = []

    from src.routes.users import users_bp
    from src.routes.reports import reports_bp
    from src.routes.settings import settings_bp
    from src.routes.auth import auth_bp

    app.register_blueprint(users_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(auth_bp)

    @app.get("/health")
    def health():
        return jsonify(status="ok", version="0.9.0")

    return app


# ── Seed data ────────────────────────────────────────────────────────────────


def _seed_employees() -> list[dict]:
    return [
        {
            "id": 1, "name": "Alice Johnson", "email": "alice@company.com",
            "department": "Engineering", "role": "Senior Engineer",
            "salary": 145000, "ssn": "123-45-6789", "phone": "(415) 555-0101",
            "hire_date": "2021-03-15", "is_active": True,
        },
        {
            "id": 2, "name": "Bob Williams", "email": "bob@company.com",
            "department": "Marketing", "role": "Marketing Manager",
            "salary": 120000, "ssn": "234-56-7890", "phone": "(415) 555-0102",
            "hire_date": "2020-07-01", "is_active": True,
        },
        {
            "id": 3, "name": "Carol Martinez", "email": "carol@company.com",
            "department": "Engineering", "role": "Tech Lead",
            "salary": 175000, "ssn": "345-67-8901", "phone": "(415) 555-0103",
            "hire_date": "2019-01-10", "is_active": True,
        },
        {
            "id": 4, "name": "David Lee", "email": "david@company.com",
            "department": "Finance", "role": "Financial Analyst",
            "salary": 110000, "ssn": "456-78-9012", "phone": "(415) 555-0104",
            "hire_date": "2022-05-20", "is_active": True,
        },
        {
            "id": 5, "name": "Eve Brown", "email": "eve@company.com",
            "department": "HR", "role": "HR Director",
            "salary": 155000, "ssn": "567-89-0123", "phone": "(415) 555-0105",
            "hire_date": "2018-11-01", "is_active": False,
        },
    ]


def _seed_reports() -> list[dict]:
    return [
        {
            "id": 1, "title": "Q1 2026 Revenue Summary",
            "type": "quarterly", "department": "Finance",
            "content": "Total revenue: $4.2M (+12% YoY). EBITDA: $1.1M.",
            "created_by": "david@company.com", "created_at": "2026-04-01",
        },
        {
            "id": 2, "title": "Engineering Headcount Report",
            "type": "headcount", "department": "Engineering",
            "content": "Total: 42 engineers. Open reqs: 8. Attrition: 4.2%.",
            "created_by": "carol@company.com", "created_at": "2026-03-28",
        },
        {
            "id": 3, "title": "Annual Budget Forecast 2026",
            "type": "annual", "department": "Finance",
            "content": "Projected spend: €18.5M. Headcount budget: €12.1M. Infra: €3.2M.",
            "created_by": "david@company.com", "created_at": "2026-01-15",
        },
        {
            "id": 4, "title": "APAC Expansion — München Office",
            "type": "quarterly", "department": "Operations",
            "content": "München office setup: €2.1M. Héctor García leading. Q3 target: ¥450M.",
            "created_by": "carol@company.com", "created_at": "2026-03-15",
        },
    ]


def _seed_settings() -> dict:
    return {
        "maintenance_mode": False,
        "max_upload_size_mb": 50,
        "session_timeout_minutes": 480,
        "allowed_ip_ranges": ["10.0.0.0/8", "172.16.0.0/12"],
        "backup_enabled": True,
        "backup_schedule": "0 2 * * *",
        "smtp_host": "smtp.company.com",
        "smtp_port": 587,
        "log_level": "INFO",
    }
