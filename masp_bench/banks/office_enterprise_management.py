"""SQLite-backed stateful backend for office / enterprise management tools."""

from __future__ import annotations

import inspect
import itertools
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_SQL = """
CREATE TABLE onboarding_packets (
    packet_key TEXT PRIMARY KEY,
    proposed_user_id TEXT NOT NULL,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT NOT NULL,
    department TEXT NOT NULL,
    title TEXT NOT NULL,
    location TEXT NOT NULL,
    cost_center TEXT NOT NULL,
    manager TEXT NOT NULL,
    requested_start_date TEXT NOT NULL,
    standard_device TEXT NOT NULL
);

CREATE TABLE employees (
    user_id TEXT PRIMARY KEY,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT NOT NULL,
    department TEXT NOT NULL,
    title TEXT NOT NULL,
    location TEXT NOT NULL,
    cost_center TEXT NOT NULL,
    manager TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE hardware_assets (
    asset_id TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    serial_number TEXT NOT NULL,
    assigned_to TEXT NOT NULL,
    warranty_expiration TEXT NOT NULL,
    condition TEXT NOT NULL,
    cost_center TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE service_catalog_requests (
    request_id TEXT PRIMARY KEY,
    item_name TEXT NOT NULL,
    requested_for TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    configuration TEXT NOT NULL,
    status TEXT NOT NULL,
    submitted_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE work_records (
    record_id TEXT PRIMARY KEY,
    record_type TEXT NOT NULL,
    query_key TEXT NOT NULL,
    category TEXT NOT NULL,
    priority TEXT NOT NULL,
    caller_name TEXT NOT NULL,
    caller_email TEXT NOT NULL,
    caller_phone TEXT NOT NULL,
    short_description TEXT NOT NULL,
    description TEXT NOT NULL,
    assignment_group TEXT NOT NULL,
    assigned_to TEXT NOT NULL,
    internal_note TEXT NOT NULL,
    status TEXT NOT NULL,
    impact TEXT NOT NULL DEFAULT '',
    risk TEXT NOT NULL DEFAULT '',
    desired_duration_days INTEGER NOT NULL DEFAULT 0,
    window_block_start TEXT NOT NULL DEFAULT '',
    window_block_end TEXT NOT NULL DEFAULT '',
    implementation_plan TEXT NOT NULL DEFAULT '',
    rollback_plan TEXT NOT NULL DEFAULT '',
    parent_record_id TEXT NOT NULL DEFAULT ''
);

CREATE TABLE change_schedules (
    change_id TEXT PRIMARY KEY,
    planned_start TEXT NOT NULL,
    planned_end TEXT NOT NULL,
    sequencing_note TEXT NOT NULL
);

CREATE TABLE knowledge_articles (
    article_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    audience TEXT NOT NULL,
    body_json TEXT NOT NULL
);

CREATE TABLE knowledge_queries (
    query_key TEXT NOT NULL,
    article_id TEXT NOT NULL
);

CREATE TABLE expense_lines (
    expense_id TEXT PRIMARY KEY,
    hashtag TEXT NOT NULL,
    amount REAL NOT NULL,
    return_score REAL NOT NULL,
    description TEXT NOT NULL,
    cost_center TEXT NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE dashboard_metrics (
    dashboard_name TEXT PRIMARY KEY,
    responsible_queue TEXT NOT NULL,
    overdue_count INTEGER NOT NULL,
    threshold INTEGER NOT NULL,
    threshold_guidance TEXT NOT NULL,
    escalation_contact TEXT NOT NULL
);

CREATE TABLE audit_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    tool TEXT NOT NULL,
    args_json TEXT NOT NULL,
    agent_role TEXT
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_money(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).strip().replace("$", "").replace(",", "")
    if not cleaned:
        return 0.0
    return float(cleaned)


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _norm(value: str) -> str:
    return value.strip().lower()


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

    def _seed(self, seed_dir: Path) -> None:
        with open(seed_dir / "seed_directory.json") as f:
            directory_seed = json.load(f)
        for packet_key, payload in directory_seed["packets"].items():
            self.conn.execute(
                "INSERT INTO onboarding_packets (packet_key, proposed_user_id, first_name, last_name, email, department, title, location, cost_center, manager, requested_start_date, standard_device) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    packet_key,
                    payload["proposed_user_id"],
                    payload["first_name"],
                    payload["last_name"],
                    payload["email"],
                    payload["department"],
                    payload["title"],
                    payload["location"],
                    payload["cost_center"],
                    payload["manager"],
                    payload["requested_start_date"],
                    payload["standard_device"],
                ),
            )
        for user_id, payload in directory_seed["employees"].items():
            self.conn.execute(
                "INSERT INTO employees (user_id, first_name, last_name, email, phone, department, title, location, cost_center, manager, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id,
                    payload["first_name"],
                    payload["last_name"],
                    payload["email"],
                    payload["phone"],
                    payload["department"],
                    payload["title"],
                    payload["location"],
                    payload["cost_center"],
                    payload["manager"],
                    payload["status"],
                ),
            )

        with open(seed_dir / "seed_assets.json") as f:
            asset_seed = json.load(f)
        for asset_id, payload in asset_seed["hardware_assets"].items():
            self.conn.execute(
                "INSERT INTO hardware_assets (asset_id, model, serial_number, assigned_to, warranty_expiration, condition, cost_center, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    asset_id,
                    payload["model"],
                    payload["serial_number"],
                    payload["assigned_to"],
                    payload["warranty_expiration"],
                    payload["condition"],
                    payload["cost_center"],
                    payload["status"],
                ),
            )

        with open(seed_dir / "seed_work_records.json") as f:
            work_seed = json.load(f)
        for record_id, payload in work_seed["work_records"].items():
            self.conn.execute(
                "INSERT INTO work_records (record_id, record_type, query_key, category, priority, caller_name, caller_email, caller_phone, short_description, description, assignment_group, assigned_to, internal_note, status, impact, risk, desired_duration_days, window_block_start, window_block_end, implementation_plan, rollback_plan, parent_record_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record_id,
                    payload["record_type"],
                    payload["query_key"],
                    payload["category"],
                    payload["priority"],
                    payload["caller_name"],
                    payload["caller_email"],
                    payload["caller_phone"],
                    payload["short_description"],
                    payload["description"],
                    payload["assignment_group"],
                    payload["assigned_to"],
                    payload["internal_note"],
                    payload["status"],
                    payload.get("impact", ""),
                    payload.get("risk", ""),
                    payload.get("desired_duration_days", 0),
                    payload.get("window_block_start", ""),
                    payload.get("window_block_end", ""),
                    payload.get("implementation_plan", ""),
                    payload.get("rollback_plan", ""),
                    payload.get("parent_record_id", ""),
                ),
            )

        with open(seed_dir / "seed_knowledge.json") as f:
            knowledge_seed = json.load(f)
        for article_id, payload in knowledge_seed["articles"].items():
            self.conn.execute(
                "INSERT INTO knowledge_articles (article_id, title, audience, body_json) VALUES (?, ?, ?, ?)",
                (
                    article_id,
                    payload["title"],
                    payload["audience"],
                    json.dumps(payload["body_lines"]),
                ),
            )
        for query_key, article_ids in knowledge_seed["queries"].items():
            for article_id in article_ids:
                self.conn.execute(
                    "INSERT INTO knowledge_queries (query_key, article_id) VALUES (?, ?)",
                    (query_key, article_id),
                )

        with open(seed_dir / "seed_expenses.json") as f:
            expense_seed = json.load(f)
        for expense_id, payload in expense_seed["expense_lines"].items():
            self.conn.execute(
                "INSERT INTO expense_lines (expense_id, hashtag, amount, return_score, description, cost_center, deleted) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    expense_id,
                    payload["hashtag"],
                    payload["amount"],
                    payload["return_score"],
                    payload["description"],
                    payload["cost_center"],
                    1 if payload.get("deleted") else 0,
                ),
            )

        with open(seed_dir / "seed_dashboards.json") as f:
            dashboard_seed = json.load(f)
        for dashboard_name, payload in dashboard_seed.items():
            self.conn.execute(
                "INSERT INTO dashboard_metrics (dashboard_name, responsible_queue, overdue_count, threshold, threshold_guidance, escalation_contact) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    dashboard_name,
                    payload["responsible_queue"],
                    payload["overdue_count"],
                    payload["threshold"],
                    payload["threshold_guidance"],
                    payload["escalation_contact"],
                ),
            )

        self.conn.commit()

    def set_current_agent(self, role: str | None) -> None:
        self._current_agent_role = role

    def dispatch(self, tool_name: str, args: dict[str, Any]) -> str:
        try:
            self.conn.execute(
                "INSERT INTO audit_log (timestamp, tool, args_json, agent_role) VALUES (?, ?, ?, ?)",
                (_now_iso(), tool_name, json.dumps(args, default=str), self._current_agent_role),
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

    def _employee_by_lookup(self, user_lookup: str) -> sqlite3.Row | None:
        row = self.conn.execute(
            "SELECT * FROM employees WHERE user_id = ?",
            (user_lookup,),
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

    def _packet_by_lookup(self, user_lookup: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM onboarding_packets WHERE packet_key = ?",
            (user_lookup,),
        ).fetchone()

    def _asset_rows_for_user(self, user_id: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM hardware_assets WHERE assigned_to = ? ORDER BY asset_id",
                (user_id,),
            ).fetchall()
        )

    def _render_incident(self, row: sqlite3.Row) -> str:
        return "\n".join(
            [
                f"Incident Record — {row['record_id']}",
                f"Category: {row['category']}",
                f"Priority: {row['priority']}",
                f"Caller: {row['caller_name']}",
                f"Caller Email: {row['caller_email']}",
                f"Caller Phone: {row['caller_phone']}",
                f"Description: {row['description']}",
                f"Current Assignment Group: {row['assignment_group']}",
                (
                    f"Assigned To: {row['assigned_to']}"
                    if row["assigned_to"]
                    else "Assigned To: (unassigned)"
                ),
                f"Internal Note: {row['internal_note']}",
            ]
        )

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
                line += (
                    f" | Planned Start: {row['planned_start']} | Planned End: {row['planned_end']}"
                )
            if row["sequencing_note"]:
                line += f" | Sequencing Note: {row['sequencing_note']}"
            lines.append(line)
        return "\n".join(lines)

    def tool_read_user_directory(self, user_lookup: str = "", **kwargs: Any) -> str:
        packet = self._packet_by_lookup(user_lookup)
        if packet:
            return "\n".join(
                [
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
                ]
            )

        employee = self._employee_by_lookup(user_lookup)
        if employee:
            return "\n".join(
                [
                    "Employee Directory Entry",
                    f"User ID: {employee['user_id']}",
                    f"Name: {employee['first_name']} {employee['last_name']}",
                    f"Email: {employee['email']}",
                    f"Phone: {employee['phone']}",
                    f"Location: {employee['location']}",
                    f"Department: {employee['department']}",
                    f"Cost Center: {employee['cost_center']}",
                ]
            )

        return f"Directory lookup returned no result for {user_lookup}."

    def tool_create_user_account(
        self,
        first_name: str = "",
        last_name: str = "",
        email: str = "",
        department: str = "",
        title: str = "",
        location: str = "",
        **kwargs: Any,
    ) -> str:
        packet = self.conn.execute(
            "SELECT * FROM onboarding_packets WHERE lower(email) = lower(?)",
            (email,),
        ).fetchone()
        user_id = (
            packet["proposed_user_id"]
            if packet
            else f"USR-{first_name.strip().upper()}-{last_name.strip().upper()}".replace(" ", "-")
        )
        existing = self.conn.execute(
            "SELECT user_id FROM employees WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if existing:
            return f"User account already exists: {user_id}"

        cost_center = packet["cost_center"] if packet else "UNASSIGNED"
        manager = packet["manager"] if packet else "Unknown Manager"
        self.conn.execute(
            "INSERT INTO employees (user_id, first_name, last_name, email, phone, department, title, location, cost_center, manager, status) "
            "VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, ?, 'provisioning')",
            (
                user_id,
                first_name,
                last_name,
                email,
                department,
                title,
                location,
                cost_center,
                manager,
            ),
        )
        self.conn.commit()
        return "\n".join(
            [
                "User Account Created",
                f"User ID: {user_id}",
                f"Email: {email}",
                f"Department: {department}",
                "Status: queued for access-provisioning review.",
            ]
        )

    def tool_query_asset_inventory(
        self,
        assigned_to: str = "",
        asset_type: str = "",
        query: str = "",
        **kwargs: Any,
    ) -> str:
        rows = self._asset_rows_for_user(assigned_to)
        if not rows:
            return f"Asset Inventory Query\nAssigned To: {assigned_to}\n(no assets found)"

        lines = [f"Asset Inventory Query", f"Assigned To: {assigned_to}"]
        for row in rows:
            lines.extend(
                [
                    f"Asset ID: {row['asset_id']}",
                    f"Model: {row['model']}",
                    f"Serial Number: {row['serial_number']}",
                    f"Warranty Expiration: {row['warranty_expiration']}",
                    f"Condition: {row['condition']}",
                    f"Cost Center: {row['cost_center']}",
                ]
            )
        return "\n".join(lines)

    def tool_create_hardware_asset(
        self,
        model: str = "",
        serial_number: str = "",
        assigned_to: str = "",
        warranty_expiration: str = "",
        cost_center: str = "",
        **kwargs: Any,
    ) -> str:
        digits = "".join(ch for ch in serial_number if ch.isdigit())
        asset_id = f"AST-{digits[-4:]}" if len(digits) >= 4 else f"AST-{self._asset_counter}"
        self._asset_counter += 1
        exists = self.conn.execute(
            "SELECT asset_id FROM hardware_assets WHERE asset_id = ?",
            (asset_id,),
        ).fetchone()
        if exists:
            asset_id = f"AST-{self._asset_counter}"
            self._asset_counter += 1

        self.conn.execute(
            "INSERT INTO hardware_assets (asset_id, model, serial_number, assigned_to, warranty_expiration, condition, cost_center, status) "
            "VALUES (?, ?, ?, ?, ?, 'newly created', ?, 'awaiting_assignment_confirmation')",
            (
                asset_id,
                model,
                serial_number,
                assigned_to,
                warranty_expiration,
                cost_center,
            ),
        )
        self.conn.commit()
        return "\n".join(
            [
                "Hardware Asset Created",
                f"Asset ID: {asset_id}",
                f"Model: {model}",
                f"Assigned To: {assigned_to}",
                f"Serial Number: {serial_number}",
                "Status: awaiting assignment confirmation.",
            ]
        )

    def tool_update_asset_assignment(
        self,
        asset_id: str = "",
        assigned_to: str = "",
        action: str = "",
        **kwargs: Any,
    ) -> str:
        row = self.conn.execute(
            "SELECT * FROM hardware_assets WHERE asset_id = ?",
            (asset_id,),
        ).fetchone()
        if not row:
            return f"Asset assignment update rejected: {asset_id} not found."

        self.conn.execute(
            "UPDATE hardware_assets SET assigned_to = ?, status = 'assigned' WHERE asset_id = ?",
            (assigned_to or row["assigned_to"], asset_id),
        )
        self.conn.commit()
        return "\n".join(
            [
                f"Asset Assignment Updated — {asset_id}",
                f"Assigned To: {assigned_to or row['assigned_to']}",
                f"Action: {action or 'assign'}",
            ]
        )

    def tool_order_service_catalog_item(
        self,
        item_name: str = "",
        requested_for: str = "",
        quantity: str = "",
        configuration: str = "",
        **kwargs: Any,
    ) -> str:
        qty = int(quantity or 1)
        self._request_counter += 1
        request_id = f"REQ-{self._request_counter}"
        self.conn.execute(
            "INSERT INTO service_catalog_requests (request_id, item_name, requested_for, quantity, configuration, status, submitted_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'awaiting fulfillment', ?, ?)",
            (
                request_id,
                item_name,
                requested_for,
                qty,
                configuration,
                self._current_agent_role or "unknown",
                _now_iso(),
            ),
        )
        self.conn.commit()
        return "\n".join(
            [
                "Service Catalog Request Submitted",
                f"Request ID: {request_id}",
                f"Item Name: {item_name}",
                f"Requested For: {requested_for}",
                f"Quantity: {qty}",
                f"Configuration: {configuration or 'default'}",
                "Status: awaiting fulfillment.",
            ]
        )

    def tool_query_work_records(
        self,
        record_type: str = "",
        query: str = "",
        fields: str = "",
        sort_by: str = "",
        **kwargs: Any,
    ) -> str:
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
        return "\n".join(
            [
                f"Work Record — {row['record_id']}",
                f"Type: {row['record_type']}",
                f"Short Description: {row['short_description']}",
                f"Status: {row['status']}",
            ]
        )

    def tool_assign_work_record(
        self,
        record_type: str = "",
        record_id: str = "",
        assigned_to: str = "",
        assignment_group: str = "",
        note: str = "",
        **kwargs: Any,
    ) -> str:
        row = self.conn.execute(
            "SELECT * FROM work_records WHERE record_id = ?",
            (record_id,),
        ).fetchone()
        if not row:
            return f"Work record assignment rejected: {record_id} not found."

        updated_note = row["internal_note"]
        if note:
            updated_note = f"{updated_note} | {note}" if updated_note else note
        self.conn.execute(
            "UPDATE work_records SET assigned_to = ?, assignment_group = ?, internal_note = ?, status = 'assigned' WHERE record_id = ?",
            (
                assigned_to or row["assigned_to"],
                assignment_group or row["assignment_group"],
                updated_note,
                record_id,
            ),
        )
        self.conn.commit()
        return "\n".join(
            [
                f"Work Record Assignment Updated — {record_id}",
                f"Assignment Group: {assignment_group or row['assignment_group']}",
                f"Assigned To: {assigned_to or row['assigned_to'] or '(none)'}",
                f"Note: {note or 'none'}",
            ]
        )

    def tool_schedule_change_request(
        self,
        change_id: str = "",
        planned_start: str = "",
        planned_end: str = "",
        sequencing_note: str = "",
        **kwargs: Any,
    ) -> str:
        row = self.conn.execute(
            "SELECT * FROM work_records WHERE record_id = ? AND record_type = 'change'",
            (change_id,),
        ).fetchone()
        if not row:
            return f"Change scheduling rejected: {change_id} not found."

        self.conn.execute(
            "INSERT OR REPLACE INTO change_schedules (change_id, planned_start, planned_end, sequencing_note) VALUES (?, ?, ?, ?)",
            (change_id, planned_start, planned_end, sequencing_note),
        )
        self.conn.execute(
            "UPDATE work_records SET status = 'scheduled' WHERE record_id = ?",
            (change_id,),
        )
        self.conn.commit()
        return "\n".join(
            [
                f"Change Request Schedule Updated — {change_id}",
                f"Planned Start: {planned_start}",
                f"Planned End: {planned_end}",
                f"Sequencing Note: {sequencing_note or 'none'}",
            ]
        )

    def tool_create_followup_record(
        self,
        record_type: str = "",
        short_description: str = "",
        payload: str = "",
        **kwargs: Any,
    ) -> str:
        self._followup_counter += 1
        prefix = "INC" if _norm(record_type) == "incident" else "WRK"
        record_id = f"{prefix}-FUP-{self._followup_counter}"
        self.conn.execute(
            "INSERT INTO work_records (record_id, record_type, query_key, category, priority, caller_name, caller_email, caller_phone, short_description, description, assignment_group, assigned_to, internal_note, status, impact, risk, desired_duration_days, window_block_start, window_block_end, implementation_plan, rollback_plan, parent_record_id) "
            "VALUES (?, ?, '', 'Follow-up', 'Medium', '', '', '', ?, ?, 'Austin Workplace Support', '', ?, 'open', '', '', 0, '', '', '', '', '')",
            (
                record_id,
                record_type or "incident",
                short_description,
                payload or short_description,
                payload or "",
            ),
        )
        self.conn.commit()
        return "\n".join(
            [
                "Follow-up Record Created",
                f"Record ID: {record_id}",
                f"Record Type: {record_type or 'incident'}",
                f"Short Description: {short_description}",
                "Status: open",
            ]
        )

    def tool_search_knowledge_base(
        self,
        query: str = "",
        audience: str = "",
        **kwargs: Any,
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
        self,
        hashtag: str = "",
        query: str = "",
        fields: str = "",
        **kwargs: Any,
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
                f"- {row['expense_id']} | Cost Center: {row['cost_center']} | Amount: {_money(float(row['amount']))} | "
                f"Return Score: {int(row['return_score'])} | Description: {row['description']}"
            )
        return "\n".join(lines)

    def tool_optimize_budget_allocation(
        self,
        hashtag: str = "",
        budget: str = "",
        objective: str = "",
        **kwargs: Any,
    ) -> str:
        rows = list(
            self.conn.execute(
                "SELECT * FROM expense_lines WHERE hashtag = ? AND deleted = 0 ORDER BY expense_id",
                (hashtag,),
            ).fetchall()
        )
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
        for expense_id in sorted(retained):
            lines.append(f"- {expense_id}")
        lines.append(f"Projected Total Return Score: {int(best_score)}")
        lines.append("Delete:")
        for expense_id in deleted:
            lines.append(f"- {expense_id}")
        lines.append("Reasoning:")
        lines.append(
            f"- {', '.join(sorted(retained))} stays within budget at {_money(best_spend)} and yields the highest combined return score."
        )
        return "\n".join(lines)

    def tool_delete_expense_line(
        self,
        expense_id: str = "",
        reason: str = "",
        **kwargs: Any,
    ) -> str:
        row = self.conn.execute(
            "SELECT * FROM expense_lines WHERE expense_id = ?",
            (expense_id,),
        ).fetchone()
        if not row:
            return f"Expense deletion rejected: {expense_id} not found."

        self.conn.execute(
            "UPDATE expense_lines SET deleted = 1 WHERE expense_id = ?",
            (expense_id,),
        )
        self.conn.commit()
        return "\n".join(
            [
                "Expense Line Deleted",
                f"Expense ID: {expense_id}",
                f"Reason: {reason or 'none provided'}",
            ]
        )

    def tool_read_dashboard_metric(
        self,
        dashboard_name: str = "",
        metric_type: str = "",
        hashtag: str = "",
        **kwargs: Any,
    ) -> str:
        row = self.conn.execute(
            "SELECT * FROM dashboard_metrics WHERE dashboard_name = ?",
            (dashboard_name,),
        ).fetchone()
        if not row:
            return f"Dashboard Metric — {dashboard_name}\nNo metric found."

        return "\n".join(
            [
                f"Dashboard Metric — {row['dashboard_name']}",
                f"Highest overdue replacement queue: {row['responsible_queue']}",
                f"Overdue replacement count: {row['overdue_count']}",
                f"Threshold guidance: {row['threshold_guidance']}",
                f"Escalation Contact: {row['escalation_contact']}",
            ]
        )
