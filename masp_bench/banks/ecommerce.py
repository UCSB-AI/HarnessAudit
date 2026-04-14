"""EcommerceBank — SQLite-backed stateful backend for the ecommerce domain."""

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
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT NOT NULL,
    shipping_address TEXT NOT NULL,
    loyalty_tier TEXT,
    account_notes TEXT,
    vip_note TEXT
);

CREATE TABLE payment_methods (
    method_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    method_type TEXT NOT NULL,
    label TEXT NOT NULL,
    balance REAL,
    is_default INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);

CREATE TABLE products (
    product_id TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE product_variants (
    variant_id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    availability TEXT NOT NULL,
    price REAL NOT NULL,
    FOREIGN KEY (product_id) REFERENCES products(product_id)
);

CREATE TABLE orders (
    order_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    status TEXT NOT NULL,
    shipping_address TEXT NOT NULL,
    delivered_at TEXT,
    tracking_number TEXT,
    case_link TEXT,
    modification_constraints_json TEXT NOT NULL DEFAULT '[]',
    last_adjustment_note TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);

CREATE TABLE order_items (
    item_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    variant_id TEXT NOT NULL,
    product_name TEXT NOT NULL,
    price REAL NOT NULL,
    resolution_status TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (order_id) REFERENCES orders(order_id)
);

CREATE TABLE payment_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    payment_method_id TEXT NOT NULL,
    amount REAL NOT NULL,
    kind TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL
);

CREATE TABLE order_resolutions (
    resolution_id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    resolution_type TEXT NOT NULL,
    item_ids_json TEXT NOT NULL,
    replacement_item_ids_json TEXT NOT NULL,
    refund_target TEXT NOT NULL,
    note TEXT NOT NULL,
    timestamp TEXT NOT NULL
);

CREATE TABLE ledger_entries (
    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    amount REAL NOT NULL,
    destination TEXT NOT NULL,
    reason TEXT NOT NULL,
    entry_type TEXT NOT NULL,
    status TEXT NOT NULL,
    timestamp TEXT NOT NULL
);

CREATE TABLE support_cases (
    case_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    priority TEXT NOT NULL,
    issue_tag TEXT NOT NULL,
    current_queue TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    email_from TEXT NOT NULL,
    email_subject TEXT NOT NULL,
    email_body TEXT NOT NULL,
    internal_notes TEXT NOT NULL,
    recommended_next_action TEXT NOT NULL
);

CREATE TABLE case_routing_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    target_queue TEXT NOT NULL,
    priority TEXT NOT NULL,
    note TEXT NOT NULL,
    timestamp TEXT NOT NULL
);

CREATE TABLE knowledge_articles (
    article_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    audience TEXT NOT NULL,
    status TEXT NOT NULL,
    body_json TEXT NOT NULL,
    reference_article_id TEXT NOT NULL DEFAULT '',
    editor_note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE knowledge_queries (
    query_key TEXT NOT NULL,
    article_id TEXT NOT NULL
);

CREATE TABLE case_trends (
    issue_tag TEXT PRIMARY KEY,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    cases_last_7d INTEGER NOT NULL,
    wow_multiplier REAL NOT NULL,
    affected_families_json TEXT NOT NULL,
    sample_customers_json TEXT NOT NULL,
    recommendation_json TEXT NOT NULL
);

CREATE TABLE opportunities (
    opportunity_id TEXT PRIMARY KEY,
    account_name TEXT NOT NULL,
    quarter_end_pressure TEXT NOT NULL,
    decision_maker TEXT NOT NULL,
    lead_phone TEXT NOT NULL,
    proposed_contract_term TEXT NOT NULL,
    requested_discount_token TEXT NOT NULL,
    sales_owner_notes_json TEXT NOT NULL
);

CREATE TABLE quotes (
    quote_id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL,
    approval_state TEXT NOT NULL,
    discount_token TEXT NOT NULL,
    contract_term TEXT NOT NULL,
    final_value REAL NOT NULL,
    decision TEXT NOT NULL DEFAULT '',
    justification TEXT NOT NULL DEFAULT ''
);

CREATE TABLE quote_line_items (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id TEXT NOT NULL,
    description TEXT NOT NULL
);

CREATE TABLE quote_risk_flags (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id TEXT NOT NULL,
    flag TEXT NOT NULL
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


def _parse_csv(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _parse_money(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).strip().replace("$", "").replace(",", "")
    if not cleaned:
        return 0.0
    return float(cleaned)


def _money(value: float) -> str:
    return f"${value:,.2f}"


class EcommerceBank:
    """SQLite-backed stateful backend for ecommerce domain tools."""

    def __init__(self, seed_dir: Path, db_path: str = ":memory:"):
        self.seed_dir = seed_dir
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA_SQL)
        self._current_agent_role: str | None = None
        self._seed(seed_dir)

    def _seed(self, seed_dir: Path) -> None:
        with open(seed_dir / "seed_customers.json") as f:
            customer_seed = json.load(f)
        for customer_id, payload in customer_seed["customers"].items():
            self.conn.execute(
                "INSERT INTO customers (customer_id, first_name, last_name, email, phone, shipping_address, loyalty_tier, account_notes, vip_note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    customer_id,
                    payload["first_name"],
                    payload["last_name"],
                    payload["email"],
                    payload["phone"],
                    payload["shipping_address"],
                    payload.get("loyalty_tier", ""),
                    payload.get("account_notes", ""),
                    payload.get("vip_note", ""),
                ),
            )
        for method_id, payload in customer_seed["payment_methods"].items():
            self.conn.execute(
                "INSERT INTO payment_methods (method_id, customer_id, method_type, label, balance, is_default) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    method_id,
                    payload["customer_id"],
                    payload["method_type"],
                    payload["label"],
                    payload.get("balance"),
                    1 if payload.get("is_default") else 0,
                ),
            )

        with open(seed_dir / "seed_products.json") as f:
            product_seed = json.load(f)
        for product_id, payload in product_seed["products"].items():
            self.conn.execute(
                "INSERT INTO products (product_id, name) VALUES (?, ?)",
                (product_id, payload["name"]),
            )
            for variant in payload.get("variants", []):
                self.conn.execute(
                    "INSERT INTO product_variants (variant_id, product_id, summary, availability, price) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        variant["variant_id"],
                        product_id,
                        variant["summary"],
                        variant["availability"],
                        variant["price"],
                    ),
                )

        with open(seed_dir / "seed_orders.json") as f:
            order_seed = json.load(f)
        for order_id, payload in order_seed["orders"].items():
            self.conn.execute(
                "INSERT INTO orders (order_id, customer_id, status, shipping_address, delivered_at, tracking_number, case_link, modification_constraints_json, last_adjustment_note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    order_id,
                    payload["customer_id"],
                    payload["status"],
                    payload["shipping_address"],
                    payload.get("delivered_at", ""),
                    payload.get("tracking_number", ""),
                    payload.get("case_link", ""),
                    json.dumps(payload.get("modification_constraints", [])),
                    "",
                ),
            )
            for item in payload.get("items", []):
                self.conn.execute(
                    "INSERT INTO order_items (item_id, order_id, product_id, variant_id, product_name, price, resolution_status, active) "
                    "VALUES (?, ?, ?, ?, ?, ?, '', 1)",
                    (
                        item["item_id"],
                        order_id,
                        item["product_id"],
                        item["variant_id"],
                        item["product_name"],
                        item["price"],
                    ),
                )
            for event in payload.get("payments", []):
                self.conn.execute(
                    "INSERT INTO payment_events (order_id, payment_method_id, amount, kind, note, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        order_id,
                        event["payment_method_id"],
                        event["amount"],
                        event["kind"],
                        event.get("note", ""),
                        _now_iso(),
                    ),
                )

        with open(seed_dir / "seed_cases.json") as f:
            case_seed = json.load(f)
        for case_id, payload in case_seed["cases"].items():
            self.conn.execute(
                "INSERT INTO support_cases (case_id, customer_id, order_id, priority, issue_tag, current_queue, summary_json, email_from, email_subject, email_body, internal_notes, recommended_next_action) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    case_id,
                    payload["customer_id"],
                    payload["order_id"],
                    payload["priority"],
                    payload["issue_tag"],
                    payload["current_queue"],
                    json.dumps(payload.get("summary_lines", [])),
                    payload["email_from"],
                    payload["email_subject"],
                    payload["email_body"],
                    payload["internal_notes"],
                    payload["recommended_next_action"],
                ),
            )
        for issue_tag, payload in case_seed["case_trends"].items():
            self.conn.execute(
                "INSERT INTO case_trends (issue_tag, start_date, end_date, cases_last_7d, wow_multiplier, affected_families_json, sample_customers_json, recommendation_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    issue_tag,
                    payload["start_date"],
                    payload["end_date"],
                    payload["cases_last_7d"],
                    payload["wow_multiplier"],
                    json.dumps(payload.get("affected_families", [])),
                    json.dumps(payload.get("sample_customers", [])),
                    json.dumps(payload.get("recommendation_lines", [])),
                ),
            )

        with open(seed_dir / "seed_knowledge.json") as f:
            knowledge_seed = json.load(f)
        for article_id, payload in knowledge_seed["articles"].items():
            self.conn.execute(
                "INSERT INTO knowledge_articles (article_id, title, audience, status, body_json, reference_article_id, editor_note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    article_id,
                    payload["title"],
                    payload["audience"],
                    payload.get("status", "published"),
                    json.dumps(payload.get("body_lines", [])),
                    payload.get("reference_article_id", ""),
                    payload.get("editor_note", ""),
                ),
            )
        for query_key, article_ids in knowledge_seed["queries"].items():
            for article_id in article_ids:
                self.conn.execute(
                    "INSERT INTO knowledge_queries (query_key, article_id) VALUES (?, ?)",
                    (query_key, article_id),
                )

        with open(seed_dir / "seed_sales.json") as f:
            sales_seed = json.load(f)
        for opportunity_id, payload in sales_seed["opportunities"].items():
            self.conn.execute(
                "INSERT INTO opportunities (opportunity_id, account_name, quarter_end_pressure, decision_maker, lead_phone, proposed_contract_term, requested_discount_token, sales_owner_notes_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    opportunity_id,
                    payload["account_name"],
                    payload["quarter_end_pressure"],
                    payload["decision_maker"],
                    payload["lead_phone"],
                    payload["proposed_contract_term"],
                    payload["requested_discount_token"],
                    json.dumps(payload.get("sales_owner_notes", [])),
                ),
            )
        for quote_id, payload in sales_seed["quotes"].items():
            self.conn.execute(
                "INSERT INTO quotes (quote_id, opportunity_id, approval_state, discount_token, contract_term, final_value, decision, justification) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    quote_id,
                    payload["opportunity_id"],
                    payload["approval_state"],
                    payload["discount_token"],
                    payload["contract_term"],
                    payload["final_value"],
                    payload.get("decision", ""),
                    payload.get("justification", ""),
                ),
            )
            for item in payload.get("line_items", []):
                self.conn.execute(
                    "INSERT INTO quote_line_items (quote_id, description) VALUES (?, ?)",
                    (quote_id, item["description"]),
                )
            for flag in payload.get("risk_flags", []):
                self.conn.execute(
                    "INSERT INTO quote_risk_flags (quote_id, flag) VALUES (?, ?)",
                    (quote_id, flag),
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

    def _customer_row(self, customer_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM customers WHERE customer_id = ?",
            (customer_id,),
        ).fetchone()

    def _payment_rows(self, customer_id: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM payment_methods WHERE customer_id = ? ORDER BY is_default DESC, method_id",
                (customer_id,),
            ).fetchall()
        )

    def _payment_display(self, row: sqlite3.Row) -> str:
        if row["method_type"] == "gift_card":
            return f"{row['method_id']} ({row['label']}, balance {_money(float(row['balance'] or 0.0))})"
        return f"{row['method_id']} ({row['label']})"

    def _variant_row(self, variant_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT pv.*, p.name AS product_name FROM product_variants pv "
            "JOIN products p ON p.product_id = pv.product_id WHERE pv.variant_id = ?",
            (variant_id,),
        ).fetchone()

    def _order_items(self, order_id: str, item_ids: list[str] | None = None) -> list[sqlite3.Row]:
        base_query = (
            "SELECT oi.*, pv.summary AS variant_summary, pv.availability AS variant_availability "
            "FROM order_items oi "
            "JOIN product_variants pv ON pv.variant_id = oi.variant_id "
            "WHERE oi.order_id = ? AND oi.active = 1"
        )
        params: list[Any] = [order_id]
        if item_ids:
            placeholders = ", ".join(["?"] * len(item_ids))
            base_query += f" AND oi.item_id IN ({placeholders})"
            params.extend(item_ids)
        base_query += " ORDER BY oi.item_id"
        return list(self.conn.execute(base_query, params).fetchall())

    def _default_card_method(self, customer_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM payment_methods WHERE customer_id = ? AND method_type = 'card' "
            "ORDER BY is_default DESC, method_id LIMIT 1",
            (customer_id,),
        ).fetchone()

    def _adjustment_context(
        self,
        order_id: str,
        item_ids: list[str],
        replacement_item_ids: list[str],
    ) -> dict[str, Any]:
        selected_items = self._order_items(order_id, item_ids or None)
        replacements = [self._variant_row(variant_id) for variant_id in replacement_item_ids]
        replacements = [row for row in replacements if row is not None]

        unused_replacements = list(replacements)
        pairings: list[tuple[sqlite3.Row, sqlite3.Row]] = []
        returns: list[sqlite3.Row] = []

        for item in selected_items:
            matched_index = next(
                (
                    idx
                    for idx, repl in enumerate(unused_replacements)
                    if repl["product_id"] == item["product_id"]
                ),
                None,
            )
            if matched_index is None:
                returns.append(item)
                continue
            pairings.append((item, unused_replacements.pop(matched_index)))

        additions = list(unused_replacements)
        current_total = sum(float(item["price"]) for item in selected_items)
        replacement_total = sum(float(repl["price"]) for _, repl in pairings) + sum(
            float(repl["price"]) for repl in additions
        )
        delta = replacement_total - current_total
        return {
            "selected_items": selected_items,
            "pairings": pairings,
            "returns": returns,
            "additions": additions,
            "delta": delta,
        }

    def _order_payment_history(self, order_id: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM payment_events WHERE order_id = ? ORDER BY seq",
                (order_id,),
            ).fetchall()
        )

    def _render_order(self, order_id: str) -> str:
        order_row = self.conn.execute(
            "SELECT * FROM orders WHERE order_id = ?",
            (order_id,),
        ).fetchone()
        if not order_row:
            return f"Order {order_id} not found."

        lines = [
            f"Order {order_row['order_id']}",
            f"Customer ID: {order_row['customer_id']}",
            f"Status: {order_row['status']}",
            f"Shipping Address: {order_row['shipping_address']}",
            "Items:",
        ]
        for item in self._order_items(order_id):
            item_line = (
                f"- Item ID: {item['item_id']} | Product ID: {item['product_id']} | "
                f"{item['product_name']} | {item['variant_summary']} | {_money(float(item['price']))}"
            )
            if item["resolution_status"]:
                item_line += f" | Resolution Status: {item['resolution_status']}"
            lines.append(item_line)

        lines.append("Payment History:")
        for event in self._order_payment_history(order_id):
            verb = "charged"
            if float(event["amount"]) < 0:
                verb = "credited"
            lines.append(
                f"- {event['payment_method_id']} {verb} {_money(abs(float(event['amount'])))}"
                + (f" ({event['note']})" if event["note"] else "")
            )

        if order_row["delivered_at"]:
            lines.append("Fulfillment:")
            lines.append(
                f"- Delivered {order_row['delivered_at']}"
                + (f" via {order_row['tracking_number']}." if order_row["tracking_number"] else ".")
            )

        if order_row["case_link"]:
            lines.append(f"Case Link: {order_row['case_link']}")

        constraints = json.loads(order_row["modification_constraints_json"])
        if constraints:
            lines.append("Modification Constraints:")
            lines.extend([f"- {constraint}" for constraint in constraints])

        if order_row["last_adjustment_note"]:
            lines.append("Latest Update:")
            lines.append(f"- {order_row['last_adjustment_note']}")

        return "\n".join(lines)

    def tool_authenticate_customer(
        self,
        email: str = "",
        first_name: str = "",
        last_name: str = "",
        zip: str = "",
        **kwargs: Any,
    ) -> str:
        row = self.conn.execute(
            "SELECT * FROM customers WHERE lower(email) = lower(?)",
            (email,),
        ).fetchone()
        if not row:
            return "Authentication failed: no customer matched the provided email."

        if first_name and row["first_name"].lower() != first_name.strip().lower():
            return "Authentication failed: first name did not match."
        if last_name and row["last_name"].lower() != last_name.strip().lower():
            return "Authentication failed: last name did not match."
        if zip and zip not in row["shipping_address"]:
            return "Authentication failed: ZIP code did not match the shipping address."

        payment_ids = ", ".join(method["method_id"] for method in self._payment_rows(row["customer_id"]))
        return "\n".join(
            [
                "Authenticated Customer",
                f"Customer ID: {row['customer_id']}",
                f"Name: {row['first_name']} {row['last_name']}",
                f"Email: {row['email']}",
                f"Shipping Address: {row['shipping_address']}",
                f"Phone: {row['phone']}",
                f"Payment Methods: {payment_ids}",
                "Status: Verified for this conversation only.",
            ]
        )

    def tool_get_customer_profile(self, customer_id: str = "", **kwargs: Any) -> str:
        row = self._customer_row(customer_id)
        if not row:
            return f"Customer {customer_id} not found."

        lines = [
            f"Customer Profile — {row['customer_id']}",
            f"Name: {row['first_name']} {row['last_name']}",
            f"Email: {row['email']}",
            f"Phone: {row['phone']}",
            f"Shipping Address: {row['shipping_address']}",
            "Preferred Payment Methods:",
        ]
        for method in self._payment_rows(customer_id):
            lines.append(f"- {self._payment_display(method)}")
        lines.append(f"Loyalty Tier: {row['loyalty_tier']}")
        if row["account_notes"]:
            lines.append(f"Account Notes: {row['account_notes']}")
        if row["vip_note"]:
            lines.append(f"VIP-NOTE: {row['vip_note']}")
        return "\n".join(lines)

    def tool_get_order_details(self, order_id: str = "", **kwargs: Any) -> str:
        return self._render_order(order_id)

    def tool_get_product_details(self, product_id: str = "", **kwargs: Any) -> str:
        product = self.conn.execute(
            "SELECT * FROM products WHERE product_id = ?",
            (product_id,),
        ).fetchone()
        if not product:
            return f"Product {product_id} not found."

        lines = [f"Product {product_id} — {product['name']}", "Variants:"]
        rows = self.conn.execute(
            "SELECT * FROM product_variants WHERE product_id = ? ORDER BY variant_id",
            (product_id,),
        ).fetchall()
        for row in rows:
            lines.append(
                f"- {row['variant_id']} | {row['summary']} | {row['availability']} | {_money(float(row['price']))}"
            )
        return "\n".join(lines)

    def tool_calculate_order_adjustment(
        self,
        order_id: str = "",
        item_ids: str = "",
        new_item_ids: str = "",
        refund_target: str = "",
        **kwargs: Any,
    ) -> str:
        order = self.conn.execute(
            "SELECT * FROM orders WHERE order_id = ?",
            (order_id,),
        ).fetchone()
        if not order:
            return f"Order {order_id} not found."

        ctx = self._adjustment_context(order_id, _parse_csv(item_ids), _parse_csv(new_item_ids))
        lines = [f"Order Adjustment Summary — {order_id}", "Requested bundle:"]
        for old_item, repl in ctx["pairings"]:
            lines.append(
                f"- Replace {old_item['item_id']} with {repl['variant_id']}"
            )
        for old_item in ctx["returns"]:
            lines.append(f"- Return {old_item['item_id']}")
        for repl in ctx["additions"]:
            lines.append(f"- Add {repl['variant_id']}")

        delta = float(ctx["delta"])
        customer_id = str(order["customer_id"])
        default_card = self._default_card_method(customer_id)
        gift_cards = [
            method["method_id"]
            for method in self._payment_rows(customer_id)
            if method["method_type"] == "gift_card"
        ]

        if delta > 0:
            lines.append(f"Net price delta: +{_money(delta)}")
            lines.append("Allowed payment targets:")
            if default_card:
                lines.append(f"- Original card {default_card['method_id']}")
            for method_id in gift_cards:
                lines.append(f"- Existing gift card {method_id}")
            recommendation = default_card["method_id"] if default_card else "the original saved card"
            lines.append(f"Recommendation: use {recommendation} for the uplift.")
        elif delta < 0:
            refund_amount = abs(delta)
            lines.append(f"Net result: refund {_money(refund_amount)} after applying the requested resolution.")
            if default_card:
                lines.append(f"Refund path: original card {default_card['method_id']} only.")
            else:
                lines.append("Refund path: original payment method only.")
        else:
            lines.append("Net result: no price delta.")
            if refund_target:
                lines.append(f"Requested settlement target: {refund_target}")
        return "\n".join(lines)

    def tool_modify_pending_order(
        self,
        order_id: str = "",
        change_scope: str = "",
        address: str = "",
        replacement_item_ids: str = "",
        payment_method_id: str = "",
        **kwargs: Any,
    ) -> str:
        order = self.conn.execute(
            "SELECT * FROM orders WHERE order_id = ?",
            (order_id,),
        ).fetchone()
        if not order:
            return f"Order {order_id} not found."
        if order["status"] != "pending":
            return f"Pending-order update rejected: {order_id} is not pending."

        ctx = self._adjustment_context(order_id, [], _parse_csv(replacement_item_ids))
        customer_id = str(order["customer_id"])
        if payment_method_id:
            payment = self.conn.execute(
                "SELECT * FROM payment_methods WHERE method_id = ? AND customer_id = ?",
                (payment_method_id, customer_id),
            ).fetchone()
            if not payment:
                return f"Pending-order update rejected: payment method {payment_method_id} is not available for {customer_id}."

        for old_item, repl in ctx["pairings"]:
            self.conn.execute(
                "UPDATE order_items SET product_id = ?, variant_id = ?, product_name = ?, price = ?, resolution_status = '' WHERE item_id = ?",
                (
                    repl["product_id"],
                    repl["variant_id"],
                    repl["product_name"],
                    repl["price"],
                    old_item["item_id"],
                ),
            )

        if address:
            self.conn.execute(
                "UPDATE orders SET shipping_address = ? WHERE order_id = ?",
                (address, order_id),
            )

        delta = float(ctx["delta"])
        note_parts: list[str] = []
        if address:
            note_parts.append(f"shipping address updated to {address}")
        if ctx["pairings"]:
            note_parts.append(
                "replacements applied: "
                + ", ".join(f"{old['item_id']}->{repl['variant_id']}" for old, repl in ctx["pairings"])
            )
        if delta > 0 and payment_method_id:
            self.conn.execute(
                "INSERT INTO payment_events (order_id, payment_method_id, amount, kind, note, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    order_id,
                    payment_method_id,
                    delta,
                    "adjustment_charge",
                    "pending-order uplift",
                    _now_iso(),
                ),
            )
            note_parts.append(f"uplift settled on {payment_method_id}")
        elif delta < 0:
            default_card = self._default_card_method(customer_id)
            destination = payment_method_id or (default_card["method_id"] if default_card else "original payment method")
            self.conn.execute(
                "INSERT INTO payment_events (order_id, payment_method_id, amount, kind, note, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    order_id,
                    destination,
                    -abs(delta),
                    "adjustment_credit",
                    "pending-order price reduction",
                    _now_iso(),
                ),
            )
            note_parts.append(f"credit issued to {destination}")

        note = "; ".join(note_parts) if note_parts else change_scope or "pending-order bundle updated"
        self.conn.execute(
            "UPDATE orders SET last_adjustment_note = ? WHERE order_id = ?",
            (note, order_id),
        )
        self.conn.commit()

        lines = [
            f"Pending Order Update — {order_id}",
            f"Status: updated and still pending fulfillment review.",
        ]
        if address:
            lines.append(f"Shipping Address Updated: {address}")
        for old_item, repl in ctx["pairings"]:
            lines.append(f"Replacement Applied: {old_item['item_id']} -> {repl['variant_id']}")
        if delta > 0:
            lines.append(f"Additional Charge: {_money(delta)} via {payment_method_id or 'saved payment method'}")
        elif delta < 0:
            lines.append(f"Credit Generated: {_money(abs(delta))}")
        else:
            lines.append("No net price delta.")
        return "\n".join(lines)

    def tool_request_post_delivery_resolution(
        self,
        order_id: str = "",
        resolution_type: str = "",
        item_ids: str = "",
        replacement_item_ids: str = "",
        refund_target: str = "",
        **kwargs: Any,
    ) -> str:
        order = self.conn.execute(
            "SELECT * FROM orders WHERE order_id = ?",
            (order_id,),
        ).fetchone()
        if not order:
            return f"Order {order_id} not found."
        if order["status"] != "delivered":
            return f"Resolution request rejected: {order_id} is not delivered."

        item_list = _parse_csv(item_ids)
        ctx = self._adjustment_context(order_id, item_list, _parse_csv(replacement_item_ids))
        note_parts: list[str] = []
        for item in ctx["returns"]:
            self.conn.execute(
                "UPDATE order_items SET resolution_status = 'return_requested' WHERE item_id = ?",
                (item["item_id"],),
            )
            note_parts.append(f"return {item['item_id']}")
        for item, repl in ctx["pairings"]:
            self.conn.execute(
                "UPDATE order_items SET resolution_status = 'exchange_requested' WHERE item_id = ?",
                (item["item_id"],),
            )
            note_parts.append(f"exchange {item['item_id']}->{repl['variant_id']}")

        resolution_note = ", ".join(note_parts) or resolution_type or "resolution requested"
        self.conn.execute(
            "INSERT INTO order_resolutions (order_id, resolution_type, item_ids_json, replacement_item_ids_json, refund_target, note, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                order_id,
                resolution_type or "mixed",
                json.dumps(item_list),
                json.dumps(_parse_csv(replacement_item_ids)),
                refund_target,
                resolution_note,
                _now_iso(),
            ),
        )
        self.conn.execute(
            "UPDATE orders SET last_adjustment_note = ? WHERE order_id = ?",
            (resolution_note, order_id),
        )
        self.conn.commit()

        lines = [
            f"Post-Delivery Resolution — {order_id}",
            f"Resolution Type: {resolution_type or 'mixed'}",
        ]
        for item, repl in ctx["pairings"]:
            lines.append(f"Exchange Requested: {item['item_id']} -> {repl['variant_id']}")
        for item in ctx["returns"]:
            lines.append(f"Return Requested: {item['item_id']}")
        if float(ctx["delta"]) < 0:
            lines.append(f"Expected Refund: {_money(abs(float(ctx['delta'])))} to {refund_target or 'the original payment method'}")
        elif float(ctx["delta"]) > 0:
            lines.append(f"Additional Settlement Required: {_money(float(ctx['delta']))}")
        lines.append("Status: request recorded. Customer will receive the next-step instructions by email.")
        return "\n".join(lines)

    def tool_issue_refund_or_store_credit(
        self,
        customer_id: str = "",
        order_id: str = "",
        amount: str = "",
        destination: str = "",
        reason: str = "",
        **kwargs: Any,
    ) -> str:
        customer = self._customer_row(customer_id)
        if not customer:
            return f"Refund / credit rejected: customer {customer_id} not found."

        value = abs(_parse_money(amount))
        resolved_destination = destination.strip() or "original card"
        entry_type = "refund"

        if resolved_destination.lower() == "original card":
            default_card = self._default_card_method(customer_id)
            resolved_destination = default_card["method_id"] if default_card else resolved_destination

        method = self.conn.execute(
            "SELECT * FROM payment_methods WHERE method_id = ?",
            (resolved_destination,),
        ).fetchone()
        if method and method["method_type"] == "gift_card":
            entry_type = "store_credit"
            new_balance = float(method["balance"] or 0.0) + value
            self.conn.execute(
                "UPDATE payment_methods SET balance = ? WHERE method_id = ?",
                (new_balance, resolved_destination),
            )
        elif "credit" in resolved_destination.lower():
            entry_type = "store_credit"

        self.conn.execute(
            "INSERT INTO ledger_entries (customer_id, order_id, amount, destination, reason, entry_type, status, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, 'recorded', ?)",
            (
                customer_id,
                order_id,
                value,
                resolved_destination,
                reason,
                entry_type,
                _now_iso(),
            ),
        )
        self.conn.commit()

        return "\n".join(
            [
                "Financial Adjustment Recorded",
                f"Customer ID: {customer_id}",
                f"Order ID: {order_id}",
                f"Type: {entry_type}",
                f"Amount: {_money(value)}",
                f"Destination: {resolved_destination}",
                f"Reason: {reason or 'n/a'}",
                "Status: pending settlement review.",
            ]
        )

    def tool_search_knowledge_articles(
        self,
        query: str = "",
        audience: str = "",
        **kwargs: Any,
    ) -> str:
        rows = self.conn.execute(
            "SELECT qa.query_key, ka.* FROM knowledge_queries qa "
            "JOIN knowledge_articles ka ON ka.article_id = qa.article_id "
            "WHERE qa.query_key = ? ORDER BY ka.article_id",
            (query,),
        ).fetchall()
        if not rows:
            return f"Knowledge Search Result\nNo article matched query: {query}"

        if query == "holiday_shipping_escalation_policy":
            outdated = next((row for row in rows if row["article_id"] == "KB-214"), None)
            corrected = next((row for row in rows if row["article_id"] == "KB-310"), None)
            lines = ["Knowledge Search Result"]
            if outdated:
                lines.append(
                    f"Outdated Article: {outdated['article_id']} — {outdated['title']}"
                )
            if corrected:
                lines.append(
                    f"Correct Article: {corrected['article_id']} — {corrected['title']}"
                )
                lines.append("Policy:")
                lines.extend(
                    [f"- {line}" for line in json.loads(corrected["body_json"])]
                )
            if outdated and outdated["reference_article_id"]:
                lines.append(
                    f"Editor Note: {outdated['article_id']} now points to {outdated['reference_article_id']}."
                )
            if outdated and outdated["editor_note"]:
                lines.append(f"KB Note: {outdated['editor_note']}")
            return "\n".join(lines)

        row = rows[0]
        lines = [
            "Knowledge Search Result",
            f"Article: {row['article_id']} — {row['title']}",
        ]
        if audience:
            lines.append(f"Audience: {audience}")
        else:
            lines.append(f"Audience: {row['audience']}")
        lines.append("Policy:")
        lines.extend([f"- {line}" for line in json.loads(row["body_json"])])
        return "\n".join(lines)

    def tool_query_case_records(
        self,
        case_id: str = "",
        customer_id: str = "",
        issue_tag: str = "",
        time_window: str = "",
        **kwargs: Any,
    ) -> str:
        row = self.conn.execute(
            "SELECT * FROM support_cases WHERE case_id = ?",
            (case_id,),
        ).fetchone()
        if not row:
            return f"Case {case_id} not found."

        lines = [
            f"Case {row['case_id']}",
            f"Customer ID: {row['customer_id']}",
            f"Priority: {row['priority']}",
            f"Issue Tag: {row['issue_tag']}",
            f"Linked Order: {row['order_id']}",
            f"Current Queue: {row['current_queue']}",
            "Summary:",
        ]
        lines.extend([f"- {line}" for line in json.loads(row["summary_json"])])
        lines.extend(
            [
                "Email Transcript Snippet:",
                f"- From: {row['email_from']}",
                f"- Subject: {row['email_subject']}",
                f"- Body: {row['email_body']}",
                "Internal Notes:",
                f"- {row['internal_notes']}",
                f"Recommended next action: {row['recommended_next_action']}",
            ]
        )

        history = self.conn.execute(
            "SELECT * FROM case_routing_events WHERE case_id = ? ORDER BY seq",
            (case_id,),
        ).fetchall()
        if history:
            lines.append("Routing History:")
            for event in history:
                lines.append(
                    f"- {event['timestamp']}: routed to {event['target_queue']} at priority {event['priority']}"
                    + (f" ({event['note']})" if event["note"] else "")
                )

        return "\n".join(lines)

    def tool_route_customer_case(
        self,
        case_id: str = "",
        target_queue: str = "",
        priority: str = "",
        note: str = "",
        **kwargs: Any,
    ) -> str:
        row = self.conn.execute(
            "SELECT * FROM support_cases WHERE case_id = ?",
            (case_id,),
        ).fetchone()
        if not row:
            return f"Case routing rejected: {case_id} not found."

        self.conn.execute(
            "UPDATE support_cases SET current_queue = ?, priority = ? WHERE case_id = ?",
            (
                target_queue or row["current_queue"],
                priority or row["priority"],
                case_id,
            ),
        )
        self.conn.execute(
            "INSERT INTO case_routing_events (case_id, target_queue, priority, note, timestamp) VALUES (?, ?, ?, ?, ?)",
            (
                case_id,
                target_queue or row["current_queue"],
                priority or row["priority"],
                note,
                _now_iso(),
            ),
        )
        self.conn.commit()

        return "\n".join(
            [
                f"Case Routing Updated — {case_id}",
                f"Queue: {target_queue or row['current_queue']}",
                f"Priority: {priority or row['priority']}",
                f"Note: {note or 'none'}",
            ]
        )

    def tool_analyze_case_trends(
        self,
        product_family: str = "",
        issue_tag: str = "",
        start_date: str = "",
        end_date: str = "",
        metric: str = "",
        **kwargs: Any,
    ) -> str:
        row = self.conn.execute(
            "SELECT * FROM case_trends WHERE issue_tag = ?",
            (issue_tag,),
        ).fetchone()
        if not row:
            return f"Trend Analysis\nNo trend data found for {issue_tag}."

        lines = [
            f"Trend Analysis — {row['issue_tag']}",
            f"Time Window: {row['start_date']} to {row['end_date']}",
            "Spike Summary:",
            f"- {row['cases_last_7d']} cases in the last 7 days, up {row['wow_multiplier']:.1f}x week-over-week.",
            "- Most affected product families: " + ", ".join(json.loads(row["affected_families_json"])) + ".",
            "- Sample affected customers: " + ", ".join(json.loads(row["sample_customers_json"])) + ".",
            "Recommendation:",
        ]
        lines.extend(
            [f"- {line}" for line in json.loads(row["recommendation_json"])]
        )
        return "\n".join(lines)

    def tool_update_knowledge_article(
        self,
        article_id: str = "",
        action: str = "",
        content: str = "",
        reference_article_id: str = "",
        **kwargs: Any,
    ) -> str:
        row = self.conn.execute(
            "SELECT * FROM knowledge_articles WHERE article_id = ?",
            (article_id,),
        ).fetchone()
        if not row:
            return f"Knowledge article update rejected: {article_id} not found."

        editor_note = content.strip() or row["editor_note"]
        self.conn.execute(
            "UPDATE knowledge_articles SET reference_article_id = ?, editor_note = ?, status = 'pending_editorial_review' WHERE article_id = ?",
            (
                reference_article_id or row["reference_article_id"],
                editor_note,
                article_id,
            ),
        )
        self.conn.commit()
        return "\n".join(
            [
                f"Knowledge Article Update Recorded — {article_id}",
                f"Action: {action or 'annotate'}",
                f"Reference Article: {reference_article_id or row['reference_article_id'] or 'none'}",
                "Status: pending editorial review before publication.",
            ]
        )

    def tool_read_sales_activity(
        self,
        opportunity_id: str = "",
        source: str = "",
        **kwargs: Any,
    ) -> str:
        row = self.conn.execute(
            "SELECT * FROM opportunities WHERE opportunity_id = ?",
            (opportunity_id,),
        ).fetchone()
        if not row:
            return f"Sales Activity — {opportunity_id}\nNo opportunity found."

        lines = [
            f"Sales Activity — {row['opportunity_id']}",
            f"Account: {row['account_name']}",
            f"Quarter-End Pressure: {row['quarter_end_pressure']}",
            f"Decision Maker: {row['decision_maker']}",
            f"Lead Phone: {row['lead_phone']}",
            "Commercial Notes:",
            f"- Proposed contract term: {row['proposed_contract_term']}",
            f"- Requested discount level: {row['requested_discount_token']}",
        ]
        lines.extend([f"- {line}" for line in json.loads(row["sales_owner_notes_json"])])
        return "\n".join(lines)

    def tool_review_quote(
        self,
        quote_id: str = "",
        opportunity_id: str = "",
        **kwargs: Any,
    ) -> str:
        row = self.conn.execute(
            "SELECT * FROM quotes WHERE quote_id = ?",
            (quote_id,),
        ).fetchone()
        if not row:
            return f"Quote Review — {quote_id}\nNo quote found."

        lines = [
            f"Quote Review — {row['quote_id']}",
            f"Opportunity: {row['opportunity_id']}",
            f"Approval State: {row['approval_state']}",
            "Line Items:",
        ]
        quote_items = self.conn.execute(
            "SELECT description FROM quote_line_items WHERE quote_id = ? ORDER BY seq",
            (quote_id,),
        ).fetchall()
        lines.extend([f"- {item['description']}" for item in quote_items])
        lines.extend(
            [
                "Commercial Terms:",
                f"- Requested discount token: {row['discount_token']}",
                f"- Contract term: {row['contract_term']}",
                f"- Final quoted value after discount: {_money(float(row['final_value']))}",
                "Risk Flags:",
            ]
        )
        flags = self.conn.execute(
            "SELECT flag FROM quote_risk_flags WHERE quote_id = ? ORDER BY seq",
            (quote_id,),
        ).fetchall()
        lines.extend([f"- {flag['flag']}" for flag in flags])
        if row["decision"]:
            lines.append(f"Recorded Decision: {row['decision']}")
        if row["justification"]:
            lines.append(f"Justification: {row['justification']}")
        return "\n".join(lines)

    def tool_approve_quote(
        self,
        quote_id: str = "",
        decision: str = "",
        justification: str = "",
        **kwargs: Any,
    ) -> str:
        row = self.conn.execute(
            "SELECT * FROM quotes WHERE quote_id = ?",
            (quote_id,),
        ).fetchone()
        if not row:
            return f"Quote approval rejected: {quote_id} not found."

        normalized = decision.strip().lower() or "escalate"
        if normalized in {"approve", "approved"}:
            approval_state = "Approved"
        elif normalized in {"reject", "rejected"}:
            approval_state = "Rejected"
        else:
            approval_state = "Escalated"

        self.conn.execute(
            "UPDATE quotes SET approval_state = ?, decision = ?, justification = ? WHERE quote_id = ?",
            (approval_state, normalized, justification, quote_id),
        )
        self.conn.commit()
        return "\n".join(
            [
                f"Quote Decision Recorded — {quote_id}",
                f"Decision: {normalized}",
                f"Approval State: {approval_state}",
                f"Justification: {justification or 'none provided'}",
            ]
        )
