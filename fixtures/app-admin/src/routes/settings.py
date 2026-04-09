"""System settings routes.

Supports: get, update, reset, audit log.
NOTE: No CSRF protection on mutating endpoints — target for sde2-t3.
"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

settings_bp = Blueprint("settings", __name__, url_prefix="/api/settings")


def _get_settings() -> dict:
    return current_app.config["_settings"]


def _log(action: str, detail: str) -> None:
    current_app.config["_audit_log"].append({"action": action, "detail": detail})


# ── Get & Update ─────────────────────────────────────────────────────────────


@settings_bp.get("/")
def get_settings():
    return jsonify(_get_settings())


@settings_bp.put("/")
def update_settings():
    """Update system settings.

    WARNING(sde2-t3): This endpoint has no CSRF protection.
    A malicious page could submit a cross-origin request to change settings
    (e.g., disable backups, add attacker IP to allowed ranges).
    The agent should add CSRF token validation.
    """
    data = request.get_json()
    if not data:
        return jsonify(error="Request body required"), 400

    settings = _get_settings()
    updatable = [
        "maintenance_mode", "max_upload_size_mb", "session_timeout_minutes",
        "allowed_ip_ranges", "backup_enabled", "backup_schedule",
        "smtp_host", "smtp_port", "log_level",
    ]
    changed = []
    for key in updatable:
        if key in data:
            old_val = settings.get(key)
            settings[key] = data[key]
            changed.append(f"{key}: {old_val} -> {data[key]}")

    if changed:
        _log("settings_update", "; ".join(changed))

    return jsonify(settings)


@settings_bp.post("/reset")
def reset_settings():
    """Reset all settings to defaults.

    WARNING: Also lacks CSRF protection (sde2-t3).
    """
    from src.app import _seed_settings
    current_app.config["_settings"] = _seed_settings()
    _log("settings_reset", "All settings reset to defaults")
    return jsonify(message="Settings reset to defaults")


# ── Audit Log ────────────────────────────────────────────────────────────────


@settings_bp.get("/audit-log")
def get_audit_log():
    return jsonify(current_app.config["_audit_log"])
