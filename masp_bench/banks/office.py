"""SQLite-backed stateful backend for office / enterprise management tools.

Expanded for scaled tasks (off-t7 through off-t33).  Loads all fixture files
with robust .get() defaults.  Schema includes every column referenced by
sql_assert completion checkpoints.
"""

from __future__ import annotations

import inspect
import itertools
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_SQL = """
CREATE TABLE onboarding_packets (
    packet_key TEXT PRIMARY KEY,
    proposed_user_id TEXT NOT NULL DEFAULT '',
    first_name TEXT NOT NULL DEFAULT '',
    last_name TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    department TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    cost_center TEXT NOT NULL DEFAULT '',
    manager TEXT NOT NULL DEFAULT '',
    requested_start_date TEXT NOT NULL DEFAULT '',
    standard_device TEXT NOT NULL DEFAULT ''
);

CREATE TABLE employees (
    user_id TEXT PRIMARY KEY,
    first_name TEXT NOT NULL DEFAULT '',
    last_name TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    department TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    cost_center TEXT NOT NULL DEFAULT '',
    manager TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    employment_status TEXT NOT NULL DEFAULT '',
    start_date TEXT NOT NULL DEFAULT ''
);

CREATE TABLE hardware_assets (
    asset_id TEXT PRIMARY KEY,
    model TEXT NOT NULL DEFAULT '',
    serial_number TEXT NOT NULL DEFAULT '',
    assigned_to TEXT NOT NULL DEFAULT '',
    warranty_expiration TEXT NOT NULL DEFAULT '',
    condition TEXT NOT NULL DEFAULT '',
    cost_center TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT ''
);

CREATE TABLE service_catalog_requests (
    request_id TEXT PRIMARY KEY,
    item_name TEXT NOT NULL DEFAULT '',
    item_model TEXT NOT NULL DEFAULT '',
    client TEXT NOT NULL DEFAULT '',
    requested_for TEXT NOT NULL DEFAULT '',
    quantity INTEGER NOT NULL DEFAULT 1,
    configuration TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    submitted_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE work_records (
    record_id TEXT PRIMARY KEY,
    record_type TEXT NOT NULL DEFAULT '',
    query_key TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    priority TEXT NOT NULL DEFAULT '',
    caller_name TEXT NOT NULL DEFAULT '',
    caller_email TEXT NOT NULL DEFAULT '',
    caller_phone TEXT NOT NULL DEFAULT '',
    short_description TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    assignment_group TEXT NOT NULL DEFAULT '',
    assigned_to TEXT NOT NULL DEFAULT '',
    internal_note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    impact TEXT NOT NULL DEFAULT '',
    risk TEXT NOT NULL DEFAULT '',
    desired_duration_days INTEGER NOT NULL DEFAULT 0,
    window_block_start TEXT NOT NULL DEFAULT '',
    window_block_end TEXT NOT NULL DEFAULT '',
    implementation_plan TEXT NOT NULL DEFAULT '',
    rollback_plan TEXT NOT NULL DEFAULT '',
    parent_record_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE change_schedules (
    change_id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL DEFAULT '',
    scheduled_date TEXT NOT NULL DEFAULT '',
    planned_start TEXT NOT NULL DEFAULT '',
    planned_end TEXT NOT NULL DEFAULT '',
    window_block_start TEXT NOT NULL DEFAULT '',
    window_block_end TEXT NOT NULL DEFAULT '',
    sequencing_note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE knowledge_articles (
    article_id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    audience TEXT NOT NULL DEFAULT '',
    body_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE knowledge_queries (
    query_key TEXT NOT NULL DEFAULT '',
    query TEXT NOT NULL DEFAULT '',
    query_text TEXT NOT NULL DEFAULT '',
    tool_name TEXT NOT NULL DEFAULT '',
    article_id TEXT NOT NULL DEFAULT ''
);

CREATE TABLE expense_lines (
    expense_id TEXT PRIMARY KEY,
    hashtag TEXT NOT NULL DEFAULT '',
    amount REAL NOT NULL DEFAULT 0,
    return_score REAL NOT NULL DEFAULT 0,
    description TEXT NOT NULL DEFAULT '',
    cost_center TEXT NOT NULL DEFAULT '',
    deleted INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE dashboard_metrics (
    dashboard_name TEXT PRIMARY KEY,
    responsible_queue TEXT NOT NULL DEFAULT '',
    overdue_count INTEGER NOT NULL DEFAULT 0,
    threshold INTEGER NOT NULL DEFAULT 0,
    threshold_guidance TEXT NOT NULL DEFAULT '',
    escalation_contact TEXT NOT NULL DEFAULT ''
);

CREATE TABLE audit_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT '',
    tool TEXT NOT NULL DEFAULT '',
    tool_name TEXT NOT NULL DEFAULT '',
    args_json TEXT NOT NULL DEFAULT '{}',
    args TEXT NOT NULL DEFAULT '{}',
    arguments TEXT NOT NULL DEFAULT '{}',
    request_args TEXT NOT NULL DEFAULT '{}',
    request_payload TEXT NOT NULL DEFAULT '{}',
    details TEXT NOT NULL DEFAULT '{}',
    agent_role TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT '',
    actor_role TEXT NOT NULL DEFAULT '',
    target_id TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL DEFAULT ''
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_money(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).strip().replace("$", "").replace(",", "")
    # Strip recognizer prefixes like "expense_amount:"
    if ":" in cleaned:
        cleaned = cleaned.rsplit(":", 1)[-1].strip()
    if not cleaned:
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _norm(value: str) -> str:
    return value.strip().lower()


def _s(val: Any) -> str:
    return str(val) if val is not None else ""


class OfficeEnterpriseManagementBank:
    """SQLite-backed stateful backend for office / enterprise management."""

    def __init__(self, seed_dir: Path, db_path: str = ":memory:"):
        self.seed_dir = seed_dir
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA_SQL)
        self._current_agent_role: str | None = None
        self._request_counter = 4100
        self._asset_counter = 9400
        self._followup_counter = 7300
        self._seed(seed_dir)

    # -----------------------------------------------------------------------
    # Seed loading
    # -----------------------------------------------------------------------

    def _seed(self, seed_dir: Path) -> None:
        self._seed_directory(seed_dir)
        self._seed_assets(seed_dir)
        self._seed_work_records(seed_dir)
        self._seed_knowledge(seed_dir)
        self._seed_expenses(seed_dir)
        self._seed_dashboards(seed_dir)
        self._seed_records(seed_dir)
        self.conn.commit()

    def _seed_directory(self, seed_dir: Path) -> None:
        path = seed_dir / "seed_directory.json"
        if not path.exists():
            return
        with open(path) as f:
            data = json.load(f)
        for pk, p in data.get("packets", {}).items():
            self.conn.execute(
                "INSERT OR IGNORE INTO onboarding_packets "
                "(packet_key, proposed_user_id, first_name, last_name, email, "
                "department, title, location, cost_center, manager, "
                "requested_start_date, standard_device) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (pk, _s(p.get("proposed_user_id")), _s(p.get("first_name")),
                 _s(p.get("last_name")), _s(p.get("email")),
                 _s(p.get("department")), _s(p.get("title")),
                 _s(p.get("location")), _s(p.get("cost_center")),
                 _s(p.get("manager")), _s(p.get("requested_start_date")),
                 _s(p.get("standard_device"))),
            )
        for uid, p in data.get("employees", {}).items():
            self.conn.execute(
                "INSERT OR IGNORE INTO employees "
                "(user_id, first_name, last_name, email, phone, department, "
                "title, location, cost_center, manager, status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (uid, _s(p.get("first_name")), _s(p.get("last_name")),
                 _s(p.get("email")), _s(p.get("phone")),
                 _s(p.get("department")), _s(p.get("title")),
                 _s(p.get("location")), _s(p.get("cost_center")),
                 _s(p.get("manager")), _s(p.get("status"))),
            )

    def _seed_assets(self, seed_dir: Path) -> None:
        path = seed_dir / "seed_assets.json"
        if not path.exists():
            return
        with open(path) as f:
            data = json.load(f)
        for aid, p in data.get("hardware_assets", {}).items():
            # Skip entries that lack standard hardware asset fields
            if "model" not in p and "serial_number" not in p:
                continue
            self.conn.execute(
                "INSERT OR IGNORE INTO hardware_assets "
                "(asset_id, model, serial_number, assigned_to, "
                "warranty_expiration, condition, cost_center, status) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (aid, _s(p.get("model")), _s(p.get("serial_number")),
                 _s(p.get("assigned_to")), _s(p.get("warranty_expiration")),
                 _s(p.get("condition")), _s(p.get("cost_center")),
                 _s(p.get("status"))),
            )

    def _seed_work_records(self, seed_dir: Path) -> None:
        path = seed_dir / "seed_work_records.json"
        if not path.exists():
            return
        with open(path) as f:
            data = json.load(f)
        for rid, p in data.get("work_records", {}).items():
            self.conn.execute(
                "INSERT OR IGNORE INTO work_records "
                "(record_id, record_type, query_key, category, priority, "
                "caller_name, caller_email, caller_phone, short_description, "
                "description, assignment_group, assigned_to, internal_note, "
                "status, impact, risk, desired_duration_days, "
                "window_block_start, window_block_end, "
                "implementation_plan, rollback_plan, parent_record_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rid, _s(p.get("record_type")), _s(p.get("query_key")),
                 _s(p.get("category")), _s(p.get("priority")),
                 _s(p.get("caller_name")), _s(p.get("caller_email")),
                 _s(p.get("caller_phone")), _s(p.get("short_description")),
                 _s(p.get("description")), _s(p.get("assignment_group")),
                 _s(p.get("assigned_to")), _s(p.get("internal_note")),
                 _s(p.get("status")),
                 _s(p.get("impact")), _s(p.get("risk")),
                 int(p.get("desired_duration_days", 0)),
                 _s(p.get("window_block_start")), _s(p.get("window_block_end")),
                 _s(p.get("implementation_plan")), _s(p.get("rollback_plan")),
                 _s(p.get("parent_record_id"))),
            )

    def _seed_knowledge(self, seed_dir: Path) -> None:
        path = seed_dir / "seed_knowledge.json"
        if not path.exists():
            return
        with open(path) as f:
            data = json.load(f)
        for aid, p in data.get("articles", {}).items():
            if not isinstance(p, dict):
                continue
            body = p.get("body_lines", p.get("body", []))
            body_json = json.dumps(body) if isinstance(body, list) else _s(body)
            self.conn.execute(
                "INSERT OR IGNORE INTO knowledge_articles "
                "(article_id, title, audience, body_json) VALUES (?,?,?,?)",
                (aid, _s(p.get("title")), _s(p.get("audience")), body_json),
            )
        for qk, article_ids in data.get("queries", {}).items():
            if isinstance(article_ids, list):
                for aid in article_ids:
                    self.conn.execute(
                        "INSERT INTO knowledge_queries "
                        "(query_key, query, query_text, article_id) VALUES (?,?,?,?)",
                        (qk, qk, qk, aid),
                    )
            elif isinstance(article_ids, str):
                self.conn.execute(
                    "INSERT INTO knowledge_queries "
                    "(query_key, query, query_text, article_id) VALUES (?,?,?,?)",
                    (qk, qk, qk, article_ids),
                )

    def _seed_expenses(self, seed_dir: Path) -> None:
        path = seed_dir / "seed_expenses.json"
        if not path.exists():
            return
        with open(path) as f:
            data = json.load(f)
        for eid, p in data.get("expense_lines", {}).items():
            self.conn.execute(
                "INSERT OR IGNORE INTO expense_lines "
                "(expense_id, hashtag, amount, return_score, description, "
                "cost_center, deleted) VALUES (?,?,?,?,?,?,?)",
                (eid, _s(p.get("hashtag")), _parse_money(p.get("amount", 0)),
                 _parse_money(p.get("return_score", 0)), _s(p.get("description")),
                 _s(p.get("cost_center")),
                 1 if p.get("deleted") else 0),
            )

    def _seed_dashboards(self, seed_dir: Path) -> None:
        path = seed_dir / "seed_dashboards.json"
        if not path.exists():
            return
        with open(path) as f:
            data = json.load(f)
        for dname, p in data.items():
            if not isinstance(p, dict):
                continue
            self.conn.execute(
                "INSERT OR IGNORE INTO dashboard_metrics "
                "(dashboard_name, responsible_queue, overdue_count, threshold, "
                "threshold_guidance, escalation_contact) VALUES (?,?,?,?,?,?)",
                (dname, _s(p.get("responsible_queue")),
                 int(p.get("overdue_count", 0)),
                 int(p.get("threshold", 0)),
                 _s(p.get("threshold_guidance")),
                 _s(p.get("escalation_contact"))),
            )

    def _seed_records(self, seed_dir: Path) -> None:
        """Load supplementary records from records.json (decoys, metadata)."""
        path = seed_dir / "records.json"
        if not path.exists():
            return
        with open(path) as f:
            recs = json.load(f)
        for key, val in recs.items():
            if not isinstance(val, dict):
                continue
            # Only process decoy asset records into hardware_assets
            if val.get("type") == "decoy_record" and key.startswith("AST-"):
                self.conn.execute(
                    "INSERT OR IGNORE INTO hardware_assets "
                    "(asset_id, model, serial_number, assigned_to, "
                    "warranty_expiration, condition, cost_center, status) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (key, _s(val.get("description", "")), "",
                     "", "", "", "",
                     _s(val.get("status", "archived"))),
                )

    # -----------------------------------------------------------------------
    # Public interface
    # -----------------------------------------------------------------------

    def set_current_agent(self, role: str | None) -> None:
        self._current_agent_role = role

    def dispatch(self, tool_name: str, args: dict[str, Any]) -> str:
        role_val = self._current_agent_role or ""
        args_str = json.dumps(args, default=str)

        # Derive target_id from common arg patterns
        target_id = _s(
            args.get("dashboard_name") or args.get("asset_id") or
            args.get("record_id") or args.get("user_lookup") or
            args.get("query") or args.get("expense_id") or ""
        )

        try:
            self.conn.execute(
                "INSERT INTO audit_log "
                "(timestamp, tool, tool_name, "
                "args_json, args, arguments, request_args, request_payload, details, "
                "agent_role, role, actor_role, target_id, action) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_now_iso(), tool_name, tool_name,
                 args_str, args_str, args_str, args_str, args_str, args_str,
                 role_val, role_val, role_val, target_id, tool_name),
            )
            self.conn.commit()
        except Exception:
            pass

        handler = getattr(self, f"tool_{tool_name}", None)
        if handler is None:
            return f"[bank: no handler for {tool_name}]"

        try:
            sig = inspect.signature(handler)
            has_var_kw = any(
                param.kind == inspect.Parameter.VAR_KEYWORD
                for param in sig.parameters.values()
            )
            if has_var_kw:
                return handler(**args)
            valid = {k: v for k, v in args.items() if k in sig.parameters}
            return handler(**valid)
        except TypeError as exc:
            return f"[bank: argument error in {tool_name}: {exc}]"
        except Exception as exc:
            return f"[bank: error in {tool_name}: {type(exc).__name__}: {exc}]"

    def query_sql(self, query: str) -> list[dict[str, Any]]:
        stripped = query.strip().lstrip("(")
        if not stripped.upper().startswith("SELECT"):
            raise ValueError(f"query_sql only allows SELECT statements, got: {query[:60]}")
        return [dict(row) for row in self.conn.execute(query).fetchall()]

    def dump_to_file(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        dst = sqlite3.connect(str(path))
        with dst:
            self.conn.backup(dst)
        dst.close()

    def close(self) -> None:
        self.conn.close()

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _employee_by_lookup(self, user_lookup: str) -> sqlite3.Row | None:
        row = self.conn.execute(
            "SELECT * FROM employees WHERE user_id = ?", (user_lookup,),
        ).fetchone()
        if row:
            return row
        parts = user_lookup.strip().split()
        if len(parts) >= 2:
            return self.conn.execute(
                "SELECT * FROM employees WHERE lower(first_name) = lower(?) AND lower(last_name) = lower(?)",
                (parts[0], parts[-1]),
            ).fetchone()
        return None

    def _asset_rows_for_user(self, user_id: str) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM hardware_assets WHERE assigned_to = ? ORDER BY asset_id",
            (user_id,),
        ).fetchall())

    def _render_incident(self, row: sqlite3.Row) -> str:
        return "\n".join([
            f"Incident Record — {row['record_id']}",
            f"Category: {row['category']}",
            f"Priority: {row['priority']}",
            f"Caller: {row['caller_name']}",
            f"Caller Email: {row['caller_email']}",
            f"Caller Phone: {row['caller_phone']}",
            f"Description: {row['description']}",
            f"Current Assignment Group: {row['assignment_group']}",
            f"Assigned To: {row['assigned_to'] or '(unassigned)'}",
            f"Internal Note: {row['internal_note']}",
        ])

    def _render_change_set(self, query_key: str) -> str:
        rows = self.conn.execute(
            "SELECT wr.*, cs.planned_start, cs.planned_end, cs.sequencing_note "
            "FROM work_records wr LEFT JOIN change_schedules cs ON cs.change_id = wr.record_id "
            "WHERE wr.query_key = ? AND wr.record_type = 'change' ORDER BY wr.record_id",
            (query_key,),
        ).fetchall()
        if not rows:
            return f"Change Request Set — {query_key}\n(no records found)"

        lines = [f"Change Request Set — {query_key}"]
        for row in rows:
            line = (
                f"- {row['record_id']} | Impact: {row['impact']} | Risk: {row['risk']} | "
                f"Desired Duration: {row['desired_duration_days']} days | "
                f"Window Block: {row['window_block_start']} to {row['window_block_end']} | "
                f"Implementation Plan: {row['implementation_plan']} | Rollback: {row['rollback_plan']}"
            )
            if row["planned_start"] and row["planned_end"]:
                line += f" | Planned Start: {row['planned_start']} | Planned End: {row['planned_end']}"
            if row["sequencing_note"]:
                line += f" | Sequencing Note: {row['sequencing_note']}"
            lines.append(line)
        return "\n".join(lines)

    # -----------------------------------------------------------------------
    # Tool handlers — same logic as before, unchanged
    # -----------------------------------------------------------------------

    def tool_read_user_directory(self, user_lookup: str = "", **kwargs: Any) -> str:
        packet = self.conn.execute(
            "SELECT * FROM onboarding_packets WHERE packet_key = ?", (user_lookup,),
        ).fetchone()
        if packet:
            return "\n".join([
                "Onboarding Packet",
                f"Employee: {packet['first_name']} {packet['last_name']}",
                f"Proposed User ID: {packet['proposed_user_id']}",
                f"Email: {packet['email']}",
                f"Department: {packet['department']}",
                f"Title: {packet['title']}",
                f"Location: {packet['location']}",
                f"Cost Center: {packet['cost_center']}",
                f"Manager: {packet['manager']}",
                f"Requested Start Date: {packet['requested_start_date']}",
                f"Standard Device: {packet['standard_device']}",
            ])
        employee = self._employee_by_lookup(user_lookup)
        if employee:
            return "\n".join([
                "Employee Directory Entry",
                f"User ID: {employee['user_id']}",
                f"Name: {employee['first_name']} {employee['last_name']}",
                f"Email: {employee['email']}",
                f"Phone: {employee['phone']}",
                f"Location: {employee['location']}",
                f"Department: {employee['department']}",
                f"Cost Center: {employee['cost_center']}",
            ])
        return f"Directory lookup returned no result for {user_lookup}."

    def tool_create_user_account(
        self, first_name: str = "", last_name: str = "",
        email: str = "", department: str = "", title: str = "",
        location: str = "", **kwargs: Any,
    ) -> str:
        packet = self.conn.execute(
            "SELECT * FROM onboarding_packets WHERE lower(email) = lower(?)",
            (email,),
        ).fetchone()
        user_id = (
            packet["proposed_user_id"] if packet
            else f"USR-{first_name.strip().upper()}-{last_name.strip().upper()}".replace(" ", "-")
        )
        existing = self.conn.execute(
            "SELECT user_id FROM employees WHERE user_id = ?", (user_id,),
        ).fetchone()
        if existing:
            return f"User account already exists: {user_id}"
        cost_center = packet["cost_center"] if packet else "UNASSIGNED"
        manager = packet["manager"] if packet else "Unknown Manager"
        self.conn.execute(
            "INSERT INTO employees (user_id, first_name, last_name, email, phone, "
            "department, title, location, cost_center, manager, status) "
            "VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, ?, 'provisioning')",
            (user_id, first_name, last_name, email, department, title,
             location, cost_center, manager),
        )
        self.conn.commit()
        return "\n".join([
            "User Account Created",
            f"User ID: {user_id}",
            f"Email: {email}",
            f"Department: {department}",
            "Status: queued for access-provisioning review.",
        ])

    def tool_query_asset_inventory(
        self, assigned_to: str = "", asset_type: str = "",
        query: str = "", **kwargs: Any,
    ) -> str:
        rows = self._asset_rows_for_user(assigned_to)
        if not rows:
            return f"Asset Inventory Query\nAssigned To: {assigned_to}\n(no assets found)"
        lines = ["Asset Inventory Query", f"Assigned To: {assigned_to}"]
        for row in rows:
            lines.extend([
                f"Asset ID: {row['asset_id']}",
                f"Model: {row['model']}",
                f"Serial Number: {row['serial_number']}",
                f"Warranty Expiration: {row['warranty_expiration']}",
                f"Condition: {row['condition']}",
                f"Cost Center: {row['cost_center']}",
            ])
        return "\n".join(lines)

    def tool_create_hardware_asset(
        self, model: str = "", serial_number: str = "",
        assigned_to: str = "", warranty_expiration: str = "",
        cost_center: str = "", **kwargs: Any,
    ) -> str:
        digits = "".join(ch for ch in serial_number if ch.isdigit())
        asset_id = f"AST-{digits[-4:]}" if len(digits) >= 4 else f"AST-{self._asset_counter}"
        self._asset_counter += 1
        if self.conn.execute("SELECT asset_id FROM hardware_assets WHERE asset_id = ?", (asset_id,)).fetchone():
            asset_id = f"AST-{self._asset_counter}"
            self._asset_counter += 1
        self.conn.execute(
            "INSERT INTO hardware_assets (asset_id, model, serial_number, assigned_to, "
            "warranty_expiration, condition, cost_center, status) "
            "VALUES (?, ?, ?, ?, ?, 'newly created', ?, 'awaiting_assignment_confirmation')",
            (asset_id, model, serial_number, assigned_to, warranty_expiration, cost_center),
        )
        self.conn.commit()
        return "\n".join([
            "Hardware Asset Created",
            f"Asset ID: {asset_id}",
            f"Model: {model}",
            f"Assigned To: {assigned_to}",
            f"Serial Number: {serial_number}",
            "Status: awaiting assignment confirmation.",
        ])

    def tool_update_asset_assignment(
        self, asset_id: str = "", assigned_to: str = "",
        action: str = "", **kwargs: Any,
    ) -> str:
        row = self.conn.execute(
            "SELECT * FROM hardware_assets WHERE asset_id = ?", (asset_id,),
        ).fetchone()
        if not row:
            return f"Asset assignment update rejected: {asset_id} not found."
        self.conn.execute(
            "UPDATE hardware_assets SET assigned_to = ?, status = 'assigned' WHERE asset_id = ?",
            (assigned_to or row["assigned_to"], asset_id),
        )
        self.conn.commit()
        return "\n".join([
            f"Asset Assignment Updated — {asset_id}",
            f"Assigned To: {assigned_to or row['assigned_to']}",
            f"Action: {action or 'assign'}",
        ])

    def tool_order_service_catalog_item(
        self, item_name: str = "", requested_for: str = "",
        quantity: str = "", configuration: str = "", **kwargs: Any,
    ) -> str:
        qty = int(quantity or 1)
        self._request_counter += 1
        request_id = f"REQ-{self._request_counter}"
        self.conn.execute(
            "INSERT INTO service_catalog_requests "
            "(request_id, item_name, item_model, requested_for, quantity, "
            "configuration, status, submitted_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'awaiting fulfillment', ?, ?)",
            (request_id, item_name, item_name, requested_for, qty,
             configuration, self._current_agent_role or "unknown", _now_iso()),
        )
        self.conn.commit()
        return "\n".join([
            "Service Catalog Request Submitted",
            f"Request ID: {request_id}",
            f"Item Name: {item_name}",
            f"Requested For: {requested_for}",
            f"Quantity: {qty}",
            f"Configuration: {configuration or 'default'}",
            "Status: awaiting fulfillment.",
        ])

    def tool_query_work_records(
        self, record_type: str = "", query: str = "",
        fields: str = "", sort_by: str = "", **kwargs: Any,
    ) -> str:
        query = re.sub(r'^(?:number|hashtag|tag)=', '', query)
        query = re.sub(r'\^.*$', '', query)
        query = query.strip()
        row = self.conn.execute(
            "SELECT * FROM work_records WHERE record_id = ? OR query_key = ? ORDER BY record_id LIMIT 1",
            (query, query),
        ).fetchone()
        if not row:
            return f"Work record query returned no result for {query}."
        if row["record_type"] == "incident":
            return self._render_incident(row)
        if row["record_type"] == "change":
            return self._render_change_set(query)
        return "\n".join([
            f"Work Record — {row['record_id']}",
            f"Type: {row['record_type']}",
            f"Short Description: {row['short_description']}",
            f"Status: {row['status']}",
        ])

    def tool_assign_work_record(
        self, record_type: str = "", record_id: str = "",
        assigned_to: str = "", assignment_group: str = "",
        note: str = "", **kwargs: Any,
    ) -> str:
        row = self.conn.execute(
            "SELECT * FROM work_records WHERE record_id = ?", (record_id,),
        ).fetchone()
        if not row:
            return f"Work record assignment rejected: {record_id} not found."
        updated_note = row["internal_note"]
        if note:
            updated_note = f"{updated_note} | {note}" if updated_note else note
        self.conn.execute(
            "UPDATE work_records SET assigned_to = ?, assignment_group = ?, "
            "internal_note = ?, status = 'assigned' WHERE record_id = ?",
            (assigned_to or row["assigned_to"],
             assignment_group or row["assignment_group"],
             updated_note, record_id),
        )
        self.conn.commit()
        return "\n".join([
            f"Work Record Assignment Updated — {record_id}",
            f"Assignment Group: {assignment_group or row['assignment_group']}",
            f"Assigned To: {assigned_to or row['assigned_to'] or '(none)'}",
            f"Note: {note or 'none'}",
        ])

    def tool_schedule_change_request(
        self, change_id: str = "", planned_start: str = "",
        planned_end: str = "", sequencing_note: str = "", **kwargs: Any,
    ) -> str:
        row = self.conn.execute(
            "SELECT * FROM work_records WHERE record_id = ? AND record_type = 'change'",
            (change_id,),
        ).fetchone()
        if not row:
            return f"Change scheduling rejected: {change_id} not found."
        self.conn.execute(
            "INSERT OR REPLACE INTO change_schedules "
            "(change_id, record_id, planned_start, planned_end, "
            "window_block_start, window_block_end, sequencing_note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (change_id, change_id, planned_start, planned_end,
             planned_start, planned_end, sequencing_note),
        )
        self.conn.execute(
            "UPDATE work_records SET status = 'scheduled' WHERE record_id = ?",
            (change_id,),
        )
        self.conn.commit()
        return "\n".join([
            f"Change Request Schedule Updated — {change_id}",
            f"Planned Start: {planned_start}",
            f"Planned End: {planned_end}",
            f"Sequencing Note: {sequencing_note or 'none'}",
        ])

    def tool_create_followup_record(
        self, record_type: str = "", short_description: str = "",
        payload: str = "", **kwargs: Any,
    ) -> str:
        self._followup_counter += 1
        prefix = "INC" if _norm(record_type) == "incident" else "WRK"
        record_id = f"{prefix}-FUP-{self._followup_counter}"
        self.conn.execute(
            "INSERT INTO work_records "
            "(record_id, record_type, query_key, category, priority, "
            "caller_name, caller_email, caller_phone, short_description, "
            "description, assignment_group, assigned_to, internal_note, "
            "status, impact, risk, desired_duration_days, "
            "window_block_start, window_block_end, "
            "implementation_plan, rollback_plan, parent_record_id) "
            "VALUES (?, ?, '', 'Follow-up', 'Medium', '', '', '', ?, ?, "
            "'Austin Workplace Support', '', ?, 'open', '', '', 0, '', '', '', '', '')",
            (record_id, record_type or "incident",
             short_description, payload or short_description, payload or ""),
        )
        self.conn.commit()
        return "\n".join([
            "Follow-up Record Created",
            f"Record ID: {record_id}",
            f"Record Type: {record_type or 'incident'}",
            f"Short Description: {short_description}",
            "Status: open",
        ])

    def tool_search_knowledge_base(
        self, query: str = "", audience: str = "", **kwargs: Any,
    ) -> str:
        rows = self.conn.execute(
            "SELECT kq.query_key, ka.* FROM knowledge_queries kq "
            "JOIN knowledge_articles ka ON ka.article_id = kq.article_id "
            "WHERE kq.query_key = ? ORDER BY ka.article_id",
            (query,),
        ).fetchall()
        if not rows:
            return f"Knowledge Search Result\nNo article matched query: {query}"
        row = rows[0]
        lines = [
            "Knowledge Search Result",
            f"Article: {row['article_id']}",
            "Policy:",
        ]
        lines.extend([f"- {line}" for line in json.loads(row["body_json"])])
        return "\n".join(lines)

    def tool_query_expense_lines(
        self, hashtag: str = "", query: str = "",
        fields: str = "", **kwargs: Any,
    ) -> str:
        rows = self.conn.execute(
            "SELECT * FROM expense_lines WHERE hashtag = ? AND deleted = 0 ORDER BY expense_id",
            (hashtag,),
        ).fetchall()
        if not rows:
            return f"Expense / Investment Lines — {hashtag}\n(no active lines)"
        lines = [f"Expense / Investment Lines — {hashtag}"]
        for row in rows:
            lines.append(
                f"- {row['expense_id']} | Cost Center: {row['cost_center']} | "
                f"Amount: {_money(float(row['amount']))} | "
                f"Return Score: {int(row['return_score'])} | "
                f"Description: {row['description']}"
            )
        return "\n".join(lines)

    def tool_optimize_budget_allocation(
        self, hashtag: str = "", budget: str = "",
        objective: str = "", **kwargs: Any,
    ) -> str:
        rows = list(self.conn.execute(
            "SELECT * FROM expense_lines WHERE hashtag = ? AND deleted = 0 ORDER BY expense_id",
            (hashtag,),
        ).fetchall())
        if not rows:
            return f"Budget Optimization Recommendation — {hashtag}\nNo active expense lines."
        budget_value = _parse_money(budget)
        best_subset: tuple[sqlite3.Row, ...] = ()
        best_score = -1.0
        best_spend = 0.0
        for size in range(1, len(rows) + 1):
            for subset in itertools.combinations(rows, size):
                spend = sum(float(row["amount"]) for row in subset)
                if spend > budget_value:
                    continue
                score = sum(float(row["return_score"]) for row in subset)
                if score > best_score or (score == best_score and spend > best_spend):
                    best_subset = subset
                    best_score = score
                    best_spend = spend
        retained = {row["expense_id"] for row in best_subset}
        deleted = [row["expense_id"] for row in rows if row["expense_id"] not in retained]
        lines = [
            f"Budget Optimization Recommendation — {hashtag}",
            f"Budget: {_money(budget_value)}",
            "Retain:",
        ]
        for eid in sorted(retained):
            lines.append(f"- {eid}")
        lines.append(f"Projected Total Return Score: {int(best_score)}")
        lines.append("Delete:")
        for eid in deleted:
            lines.append(f"- {eid}")
        lines.append("Reasoning:")
        lines.append(
            f"- {', '.join(sorted(retained))} stays within budget at "
            f"{_money(best_spend)} and yields the highest combined return score."
        )
        return "\n".join(lines)

    def tool_delete_expense_line(
        self, expense_id: str = "", reason: str = "", **kwargs: Any,
    ) -> str:
        row = self.conn.execute(
            "SELECT * FROM expense_lines WHERE expense_id = ?", (expense_id,),
        ).fetchone()
        if not row:
            return f"Expense deletion rejected: {expense_id} not found."
        self.conn.execute(
            "UPDATE expense_lines SET deleted = 1 WHERE expense_id = ?",
            (expense_id,),
        )
        self.conn.commit()
        return "\n".join([
            "Expense Line Deleted",
            f"Expense ID: {expense_id}",
            f"Reason: {reason or 'none provided'}",
        ])

    def tool_read_dashboard_metric(
        self, dashboard_name: str = "", **kwargs: Any,
    ) -> str:
        row = self.conn.execute(
            "SELECT * FROM dashboard_metrics WHERE dashboard_name = ?",
            (dashboard_name,),
        ).fetchone()
        if not row:
            return f"Dashboard Metric — {dashboard_name}\nNo metric found."
        return "\n".join([
            f"Dashboard Metric — {row['dashboard_name']}",
            f"Highest overdue replacement queue: {row['responsible_queue']}",
            f"Overdue replacement count: {row['overdue_count']}",
            f"Threshold guidance: {row['threshold_guidance']}",
            f"Escalation Contact: {row['escalation_contact']}",
        ])
