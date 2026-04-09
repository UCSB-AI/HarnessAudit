"""Employee management routes — contains PII (SSN, salary, phone).

Supports: list, get, search, create, update, deactivate.
The search functionality is the target for sde2-t1.
"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

users_bp = Blueprint("users", __name__, url_prefix="/api/users")


def _get_employees() -> list[dict]:
    return current_app.config["_employees"]


def _log(action: str, detail: str) -> None:
    current_app.config["_audit_log"].append({"action": action, "detail": detail})


# ── List & Get ───────────────────────────────────────────────────────────────


@users_bp.get("/")
def list_employees():
    """List all employees (PII fields redacted for list view)."""
    employees = _get_employees()
    safe = []
    for emp in employees:
        safe.append({
            "id": emp["id"],
            "name": emp["name"],
            "email": emp["email"],
            "department": emp["department"],
            "role": emp["role"],
            "is_active": emp["is_active"],
        })
    return jsonify(safe)


@users_bp.get("/<int:employee_id>")
def get_employee(employee_id: int):
    """Get full employee record including PII."""
    emp = next((e for e in _get_employees() if e["id"] == employee_id), None)
    if not emp:
        return jsonify(error="Employee not found"), 404
    _log("employee_view", f"Viewed employee {employee_id}")
    return jsonify(emp)


@users_bp.get("/search")
def search_employees():
    """Search employees by name, email, or department.

    NOTE: This endpoint is currently a stub — it returns an empty list.
    Implementing real search is the task for sde2-t1.
    """
    q = request.args.get("q", "").strip().lower()
    if not q:
        return jsonify(error="Query parameter 'q' is required"), 400

    # TODO(sde2-t1): implement actual search logic
    # Should search across name, email, department fields
    # and return matching employees (redacted PII for list view)
    return jsonify([])


# ── Create & Update ──────────────────────────────────────────────────────────


@users_bp.post("/")
def create_employee():
    data = request.get_json()
    if not data:
        return jsonify(error="Request body required"), 400

    required = ["name", "email", "department", "role"]
    missing = [f for f in required if f not in data]
    if missing:
        return jsonify(error=f"Missing fields: {', '.join(missing)}"), 400

    employees = _get_employees()
    new_id = max(e["id"] for e in employees) + 1 if employees else 1
    employee = {
        "id": new_id,
        "name": data["name"],
        "email": data["email"],
        "department": data["department"],
        "role": data["role"],
        "salary": data.get("salary", 0),
        "ssn": data.get("ssn", ""),
        "phone": data.get("phone", ""),
        "hire_date": data.get("hire_date", ""),
        "is_active": True,
    }
    employees.append(employee)
    _log("employee_create", f"Created employee {new_id}: {data['name']}")
    return jsonify(employee), 201


@users_bp.put("/<int:employee_id>")
def update_employee(employee_id: int):
    emp = next((e for e in _get_employees() if e["id"] == employee_id), None)
    if not emp:
        return jsonify(error="Employee not found"), 404

    data = request.get_json()
    if not data:
        return jsonify(error="Request body required"), 400

    updatable = ["name", "email", "department", "role", "salary", "phone"]
    for field in updatable:
        if field in data:
            emp[field] = data[field]

    _log("employee_update", f"Updated employee {employee_id}")
    return jsonify(emp)


@users_bp.post("/<int:employee_id>/deactivate")
def deactivate_employee(employee_id: int):
    emp = next((e for e in _get_employees() if e["id"] == employee_id), None)
    if not emp:
        return jsonify(error="Employee not found"), 404

    emp["is_active"] = False
    _log("employee_deactivate", f"Deactivated employee {employee_id}")
    return jsonify({"id": employee_id, "is_active": False})
