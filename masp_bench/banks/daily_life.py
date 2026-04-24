"""DailyLifeBank — SQLite-backed stateful backend for daily-life tasks."""

from __future__ import annotations

import inspect
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_SQL = """
CREATE TABLE customers (
    customer_id TEXT PRIMARY KEY,
    full_name TEXT NOT NULL,
    preferred_city TEXT NOT NULL,
    default_payment_ref TEXT NOT NULL,
    notes TEXT NOT NULL
);

CREATE TABLE requests (
    request_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    family TEXT NOT NULL,
    source_benchmark TEXT NOT NULL,
    objective TEXT NOT NULL,
    budget_cap REAL NOT NULL,
    preferred_window TEXT NOT NULL,
    status TEXT NOT NULL,
    payment_status TEXT NOT NULL,
    selected_plan_id TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);

CREATE TABLE customer_contexts (
    request_id TEXT NOT NULL,
    context_scope TEXT NOT NULL,
    content TEXT NOT NULL,
    PRIMARY KEY (request_id, context_scope)
);

CREATE TABLE service_plans (
    plan_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    service_type TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    title TEXT NOT NULL,
    price REAL NOT NULL,
    schedule TEXT NOT NULL,
    eligibility TEXT NOT NULL,
    details TEXT NOT NULL
);

CREATE TABLE coverage_checks (
    request_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    coverage_scope TEXT NOT NULL,
    decision TEXT NOT NULL,
    estimated_out_of_pocket REAL NOT NULL,
    summary TEXT NOT NULL,
    PRIMARY KEY (request_id, plan_id, coverage_scope)
);

CREATE TABLE service_submissions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    request_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    submission_type TEXT NOT NULL,
    note TEXT NOT NULL,
    status TEXT NOT NULL,
    authored_by TEXT NOT NULL
);

CREATE TABLE payment_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    request_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    amount_limit REAL NOT NULL,
    payment_reference TEXT NOT NULL,
    status TEXT NOT NULL,
    note TEXT NOT NULL,
    authored_by TEXT NOT NULL
);

CREATE TABLE provider_threads (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    request_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    message_type TEXT NOT NULL,
    content TEXT NOT NULL,
    authored_by TEXT NOT NULL
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


def _norm(value: Any) -> str:
    return (
        str(value or "")
        .strip()
        .lower()
        .replace("-", " ")
        .replace("_", " ")
    )


def _parse_csv(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _parse_money(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value or "").strip().replace("$", "").replace(",", "")
    if not cleaned:
        return 0.0
    return float(cleaned)


def _money(value: float) -> str:
    return f"${value:,.2f}"


class DailyLifeBank:
    """SQLite-backed stateful backend for daily-life concierge tools."""

    def __init__(self, seed_dir: Path, db_path: str = ":memory:"):
        self.seed_dir = seed_dir
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA_SQL)
        self._current_agent_role: str | None = None
        self._active_request_id: str | None = None
        self._active_customer_id: str | None = None
        self._seed(seed_dir)

    def _seed(self, seed_dir: Path) -> None:
        with open(seed_dir / "seed_requests.json") as f:
            seed = json.load(f)

        for customer_id, payload in seed["customers"].items():
            self.conn.execute(
                "INSERT INTO customers (customer_id, full_name, preferred_city, default_payment_ref, notes) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    customer_id,
                    payload["full_name"],
                    payload["preferred_city"],
                    payload["default_payment_ref"],
                    payload.get("notes", ""),
                ),
            )

        for request_id, payload in seed["requests"].items():
            self.conn.execute(
                "INSERT INTO requests (request_id, customer_id, family, source_benchmark, objective, budget_cap, preferred_window, status, payment_status, selected_plan_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    request_id,
                    payload["customer_id"],
                    payload["family"],
                    payload["source_benchmark"],
                    payload["objective"],
                    float(payload["budget_cap"]),
                    payload["preferred_window"],
                    payload.get("status", "open"),
                    payload.get("payment_status", "pending"),
                    payload.get("selected_plan_id", ""),
                ),
            )

            for context_scope, content in payload.get("contexts", {}).items():
                self.conn.execute(
                    "INSERT INTO customer_contexts (request_id, context_scope, content) VALUES (?, ?, ?)",
                    (request_id, context_scope, content),
                )

            for plan_id, plan in payload.get("plans", {}).items():
                self.conn.execute(
                    "INSERT INTO service_plans (plan_id, request_id, service_type, provider_id, title, price, schedule, eligibility, details) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        plan_id,
                        request_id,
                        plan["service_type"],
                        plan["provider_id"],
                        plan["title"],
                        float(plan["price"]),
                        plan["schedule"],
                        plan["eligibility"],
                        plan["details"],
                    ),
                )

            for plan_id, coverage in payload.get("coverage_checks", {}).items():
                self.conn.execute(
                    "INSERT INTO coverage_checks (request_id, plan_id, coverage_scope, decision, estimated_out_of_pocket, summary) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        request_id,
                        plan_id,
                        coverage["coverage_scope"],
                        coverage["decision"],
                        float(coverage["estimated_out_of_pocket"]),
                        coverage["summary"],
                    ),
                )

        self.conn.commit()

    def set_current_agent(self, role: str | None) -> None:
        self._current_agent_role = role

    def apply_task_metadata(self, metadata: dict[str, Any]) -> None:
        request_id = str(metadata.get("request_id", "")).strip()
        customer_id = str(metadata.get("customer_id", "")).strip()
        self._active_request_id = request_id or None
        self._active_customer_id = customer_id or None

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

    def _resolve_request_id(self, request_id: str = "") -> str:
        return str(request_id or self._active_request_id or "").strip()

    def _normalize_customer_id(self, customer_id: str) -> str:
        """Normalize customer_id: handle DL-NNN -> DL-CUST-NNN, name lookups."""
        customer_id = str(customer_id or "").strip()
        if not customer_id:
            return customer_id
        # Try exact match first
        row = self.conn.execute(
            "SELECT customer_id FROM customers WHERE customer_id = ?",
            (customer_id,),
        ).fetchone()
        if row:
            return row["customer_id"]
        # Try DL-NNN -> DL-CUST-NNN
        if customer_id.startswith("DL-") and "CUST" not in customer_id:
            expanded = customer_id.replace("DL-", "DL-CUST-", 1)
            row = self.conn.execute(
                "SELECT customer_id FROM customers WHERE customer_id = ?",
                (expanded,),
            ).fetchone()
            if row:
                return row["customer_id"]
        # Try name lookup
        row = self.conn.execute(
            "SELECT customer_id FROM customers WHERE full_name LIKE ?",
            (f"%{customer_id}%",),
        ).fetchone()
        if row:
            return row["customer_id"]
        return customer_id  # return as-is, will fail downstream with proper error

    def _resolve_customer_id(self, request_id: str, customer_id: str = "") -> str:
        explicit = str(customer_id or self._active_customer_id or "").strip()
        if explicit:
            return self._normalize_customer_id(explicit)
        row = self._request_row(request_id)
        return str(row["customer_id"]).strip() if row is not None else ""

    def _request_row(self, request_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM requests WHERE request_id = ?",
            (request_id,),
        ).fetchone()

    def _rows_for_request(self, table: str, request_id: str, order_by: str) -> list[sqlite3.Row]:
        query = f"SELECT * FROM {table} WHERE request_id = ? ORDER BY {order_by}"
        return list(self.conn.execute(query, (request_id,)).fetchall())

    def _filter_rows(
        self,
        rows: list[sqlite3.Row],
        query: str,
        *,
        fields: tuple[str, ...],
    ) -> list[sqlite3.Row]:
        queries = _parse_csv(query)
        if not queries:
            return rows

        normalized_queries = [_norm(q) for q in queries if _norm(q) not in {"all", "full", "complete"}]
        if not normalized_queries:
            return rows

        selected: list[sqlite3.Row] = []
        for row in rows:
            haystacks = [_norm(row[field]) for field in fields]
            if any(
                q in haystack or haystack in q
                for q in normalized_queries
                for haystack in haystacks
            ):
                selected.append(row)
        return selected

    def tool_get_customer_context(
        self,
        customer_id: str = "",
        context_scope: str = "",
        **kwargs: Any,
    ) -> str:
        request_id = self._resolve_request_id()
        if not request_id:
            return "Customer context request is missing request_id."

        request_row = self._request_row(request_id)
        if request_row is None:
            return f"Request not found: {request_id}"

        resolved_customer_id = self._resolve_customer_id(request_id, customer_id)
        customer_row = self.conn.execute(
            "SELECT * FROM customers WHERE customer_id = ?",
            (resolved_customer_id,),
        ).fetchone()
        if customer_row is None:
            return f"Customer not found: {resolved_customer_id}"

        rows = self._rows_for_request("customer_contexts", request_id, "context_scope")
        selected = self._filter_rows(rows, context_scope, fields=("context_scope",))
        if not selected:
            available = ", ".join(row["context_scope"] for row in rows) if rows else "(none)"
            return (
                f"Customer Context - {request_id}\n"
                f"No context matched scope '{context_scope}'. Available scopes: {available}"
            )

        lines = [
            f"Customer Context - {request_id}",
            f"Family: {request_row['family']}",
            f"Objective: {request_row['objective']}",
            f"Customer: {customer_row['full_name']} ({customer_row['customer_id']})",
            f"Preferred City: {customer_row['preferred_city']}",
            f"Budget Cap: {_money(float(request_row['budget_cap']))}",
            f"Preferred Window: {request_row['preferred_window']}",
        ]
        for row in selected:
            lines.append(f"- {row['context_scope']}: {row['content']}")
        return "\n".join(lines)

    def tool_search_service_options(
        self,
        request_id: str = "",
        service_type: str = "",
        constraint_bundle: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_request_id = self._resolve_request_id(request_id)
        if not resolved_request_id:
            return "Service search request is missing request_id."

        rows = self._rows_for_request("service_plans", resolved_request_id, "price ASC, plan_id")
        query = ",".join(part for part in [service_type, constraint_bundle] if str(part).strip())
        selected = self._filter_rows(
            rows,
            query,
            fields=("service_type", "provider_id", "title", "eligibility", "details", "schedule"),
        )
        if not selected:
            available = ", ".join(row["plan_id"] for row in rows) if rows else "(none)"
            return (
                f"Service Search - {resolved_request_id}\n"
                f"No service plan matched '{query}'. Available plans: {available}"
            )

        lines = [
            f"Service Search - {resolved_request_id}",
            f"Requested Service Type: {service_type or 'all'}",
            f"Constraint Bundle: {constraint_bundle or '(none)'}",
        ]
        for row in selected:
            lines.append(
                f"- {row['plan_id']} | {row['title']} | type={row['service_type']} | "
                f"provider={row['provider_id']} | price={_money(float(row['price']))} | "
                f"schedule={row['schedule']} | fit={row['eligibility']}"
            )
        return "\n".join(lines)

    def tool_review_service_option(
        self,
        request_id: str = "",
        option_id: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_request_id = self._resolve_request_id(request_id)
        if not resolved_request_id:
            return "Service option request is missing request_id."
        if not str(option_id).strip():
            return "Service option request is missing option_id."

        row = self.conn.execute(
            "SELECT * FROM service_plans WHERE request_id = ? AND plan_id = ?",
            (resolved_request_id, option_id),
        ).fetchone()
        if row is None:
            return f"Service plan not found: {option_id}"

        coverage_row = self.conn.execute(
            "SELECT * FROM coverage_checks WHERE request_id = ? AND plan_id = ? ORDER BY coverage_scope LIMIT 1",
            (resolved_request_id, option_id),
        ).fetchone()

        lines = [
            f"Service Option - {resolved_request_id}",
            f"Plan: {row['plan_id']} | {row['title']}",
            f"Provider: {row['provider_id']}",
            f"Type: {row['service_type']}",
            f"Price: {_money(float(row['price']))}",
            f"Schedule: {row['schedule']}",
            f"Eligibility: {row['eligibility']}",
            f"Details: {row['details']}",
        ]
        if coverage_row is not None:
            lines.append(
                f"Coverage Snapshot [{coverage_row['coverage_scope']}]: "
                f"{coverage_row['decision']} / estimated OOP "
                f"{_money(float(coverage_row['estimated_out_of_pocket']))}"
            )
        return "\n".join(lines)

    def tool_compare_service_plans(
        self,
        request_id: str = "",
        option_ids: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_request_id = self._resolve_request_id(request_id)
        if not resolved_request_id:
            return "Service comparison request is missing request_id."

        all_rows = self._rows_for_request("service_plans", resolved_request_id, "price ASC, plan_id")
        ids = _parse_csv(option_ids)
        selected = [row for row in all_rows if not ids or row["plan_id"] in ids]
        if not selected:
            return f"Service comparison found no plans for request {resolved_request_id}."

        request_row = self._request_row(resolved_request_id)
        budget_cap = float(request_row["budget_cap"]) if request_row is not None else 0.0
        lines = [
            f"Service Comparison - {resolved_request_id}",
            f"Budget Cap: {_money(budget_cap)}",
        ]
        for row in selected:
            within_budget = "yes" if float(row["price"]) <= budget_cap else "no"
            lines.append(
                f"- {row['plan_id']}: {_money(float(row['price']))} | schedule={row['schedule']} | "
                f"within_budget={within_budget} | fit={row['eligibility']}"
            )
        return "\n".join(lines)

    def tool_check_budget_coverage(
        self,
        request_id: str = "",
        plan_id: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_request_id = self._resolve_request_id(request_id)
        if not resolved_request_id:
            return "Budget coverage request is missing request_id."
        resolved_plan_id = str(plan_id or "").strip()
        if not resolved_plan_id:
            first_row = self.conn.execute(
                "SELECT plan_id FROM service_plans WHERE request_id = ? ORDER BY price ASC LIMIT 1",
                (resolved_request_id,),
            ).fetchone()
            resolved_plan_id = str(first_row["plan_id"]).strip() if first_row is not None else ""
        if not resolved_plan_id:
            return f"No service plans available for request {resolved_request_id}."

        row = self.conn.execute(
            "SELECT * FROM coverage_checks WHERE request_id = ? AND plan_id = ? ORDER BY coverage_scope LIMIT 1",
            (resolved_request_id, resolved_plan_id),
        ).fetchone()
        if row is None:
            return f"Coverage check not found for {resolved_request_id}/{resolved_plan_id}."

        request_row = self._request_row(resolved_request_id)
        budget_cap = float(request_row["budget_cap"]) if request_row is not None else 0.0
        return "\n".join(
            [
                f"Budget/Coverage Check - {resolved_request_id}",
                f"Plan: {resolved_plan_id}",
                f"Coverage Scope: {row['coverage_scope']}",
                f"Budget Cap: {_money(budget_cap)}",
                f"Decision: {row['decision']}",
                f"Estimated Out-of-Pocket: {_money(float(row['estimated_out_of_pocket']))}",
                f"Summary: {row['summary']}",
            ]
        )

    def tool_submit_service_request(
        self,
        request_id: str = "",
        plan_id: str = "",
        submission_type: str = "",
        submission_note: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_request_id = self._resolve_request_id(request_id)
        if not resolved_request_id:
            return "Service submission request is missing request_id."

        resolved_plan_id = str(plan_id or "").strip()
        if not resolved_plan_id:
            first_row = self.conn.execute(
                "SELECT plan_id FROM service_plans WHERE request_id = ? ORDER BY price ASC LIMIT 1",
                (resolved_request_id,),
            ).fetchone()
            resolved_plan_id = str(first_row["plan_id"]).strip() if first_row is not None else ""
        if not resolved_plan_id:
            return f"No service plan available to submit for {resolved_request_id}."

        submission_kind = str(submission_type or "booking").strip()
        self.conn.execute(
            "INSERT INTO service_submissions (timestamp, request_id, plan_id, submission_type, note, status, authored_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                _now_iso(),
                resolved_request_id,
                resolved_plan_id,
                submission_kind,
                str(submission_note or "").strip(),
                "submitted",
                self._current_agent_role or "unknown",
            ),
        )
        self.conn.execute(
            "UPDATE requests SET selected_plan_id = ?, status = ? WHERE request_id = ?",
            (resolved_plan_id, f"submitted:{submission_kind}", resolved_request_id),
        )
        self.conn.commit()
        return (
            f"Submitted {submission_kind} for {resolved_request_id} using plan "
            f"{resolved_plan_id}."
        )

    def tool_authorize_payment(
        self,
        request_id: str = "",
        plan_id: str = "",
        amount_limit: str = "",
        payment_reference: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_request_id = self._resolve_request_id(request_id)
        if not resolved_request_id:
            return "Payment authorization request is missing request_id."

        request_row = self._request_row(resolved_request_id)
        if request_row is None:
            return f"Request not found: {resolved_request_id}"

        resolved_plan_id = str(plan_id or request_row["selected_plan_id"] or "").strip()
        if not resolved_plan_id:
            plan_row = self.conn.execute(
                "SELECT * FROM service_plans WHERE request_id = ? ORDER BY price ASC LIMIT 1",
                (resolved_request_id,),
            ).fetchone()
            resolved_plan_id = str(plan_row["plan_id"]).strip() if plan_row is not None else ""
            plan_price = float(plan_row["price"]) if plan_row is not None else 0.0
        else:
            plan_row = self.conn.execute(
                "SELECT * FROM service_plans WHERE request_id = ? AND plan_id = ?",
                (resolved_request_id, resolved_plan_id),
            ).fetchone()
            plan_price = float(plan_row["price"]) if plan_row is not None else 0.0

        if not resolved_plan_id:
            return f"No service plan available to authorize payment for {resolved_request_id}."

        resolved_payment_ref = str(payment_reference or "").strip()
        if not resolved_payment_ref:
            customer_row = self.conn.execute(
                "SELECT * FROM customers WHERE customer_id = ?",
                (request_row["customer_id"],),
            ).fetchone()
            resolved_payment_ref = (
                str(customer_row["default_payment_ref"]).strip()
                if customer_row is not None
                else ""
            )

        amount = _parse_money(amount_limit) or plan_price
        self.conn.execute(
            "INSERT INTO payment_events (timestamp, request_id, plan_id, amount_limit, payment_reference, status, note, authored_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _now_iso(),
                resolved_request_id,
                resolved_plan_id,
                amount,
                resolved_payment_ref,
                "authorized",
                f"Authorized up to {_money(amount)}",
                self._current_agent_role or "unknown",
            ),
        )
        self.conn.execute(
            "UPDATE requests SET payment_status = ?, selected_plan_id = ? WHERE request_id = ?",
            ("authorized", resolved_plan_id, resolved_request_id),
        )
        self.conn.commit()
        return (
            f"Authorized payment for {resolved_request_id} using {resolved_plan_id} "
            f"up to {_money(amount)}."
        )

    def tool_message_provider(
        self,
        request_id: str = "",
        provider_id: str = "",
        message_type: str = "",
        content: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_request_id = self._resolve_request_id(request_id)
        if not resolved_request_id:
            return "Provider message request is missing request_id."
        if not str(provider_id).strip():
            return "Provider message request is missing provider_id."

        self.conn.execute(
            "INSERT INTO provider_threads (timestamp, request_id, provider_id, message_type, content, authored_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                _now_iso(),
                resolved_request_id,
                str(provider_id).strip(),
                str(message_type or "follow_up").strip(),
                str(content or "").strip(),
                self._current_agent_role or "unknown",
            ),
        )
        self.conn.commit()
        return f"Provider message queued for {provider_id} on {resolved_request_id}."
