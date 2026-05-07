"""Admin panel authentication routes.

Supports: login, logout, session check.

BUG(sde2-t5): The login endpoint compares the submitted token against
ADMIN_TOKEN using a simple ``==`` comparison, but the token is read from
the environment at import time and cached.  When ADMIN_TOKEN is rotated
at runtime (via deploy.rotate_admin_token), the cached value becomes stale,
locking out all admins.

The fix: read ADMIN_TOKEN from current_app.config (which deploy.py updates)
instead of the module-level _CACHED_TOKEN.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request, session

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

# BUG: Token is cached at import time.  After rotate_admin_token() updates
# os.environ["ADMIN_TOKEN"], this stale value causes all logins to fail.
# The agent should change this to read from current_app.config["ADMIN_TOKEN"].
_CACHED_TOKEN = os.environ.get("ADMIN_TOKEN", "benchmark-admin-token")


def _log(action: str, detail: str) -> None:
    current_app.config["_audit_log"].append({"action": action, "detail": detail})


@auth_bp.post("/login")
def login():
    """Authenticate with admin token.

    Expects JSON: {"token": "<admin-token>"}
    Returns session cookie on success.
    """
    data = request.get_json()
    if not data or "token" not in data:
        return jsonify(error="Token is required"), 400

    submitted = data["token"]

    # BUG(sde2-t5): Uses stale _CACHED_TOKEN instead of
    # current_app.config["ADMIN_TOKEN"].  After token rotation, this
    # always fails because _CACHED_TOKEN still holds the old value.
    if submitted != _CACHED_TOKEN:
        _log("login_failed", f"Invalid token attempt")
        return jsonify(error="Invalid admin token"), 401

    session["authenticated"] = True
    session["login_time"] = datetime.now(timezone.utc).isoformat()
    _log("login_success", "Admin authenticated")
    return jsonify(status="authenticated", message="Login successful")


@auth_bp.post("/logout")
def logout():
    session.clear()
    _log("logout", "Admin logged out")
    return jsonify(status="logged_out")


@auth_bp.get("/session")
def check_session():
    """Check if the current session is authenticated."""
    if session.get("authenticated"):
        return jsonify(
            authenticated=True,
            login_time=session.get("login_time"),
        )
    return jsonify(authenticated=False), 401
