"""FinanceBank — SQLite-backed stateful backend for the finance domain.

Holds per-run state (prices, portfolios, positions, risk limits, trade log,
compliance reports, audit log) in an in-memory SQLite database. Dispatches
tool calls to handler methods, auto-audits every call, and supports SQL-based
verification via query_sql().

Lifecycle:
    bank = FinanceBank(seed_dir)           # loads seed data + fixtures
    bank.set_current_agent("research_analyst")
    result = bank.dispatch("get_market_data", {"tickers": "AAPL,MSFT"})
    ...
    bank.dump_to_file(Path("results/run.sqlite"))   # persist for inspection
"""

from __future__ import annotations

import inspect
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE prices (
    ticker TEXT PRIMARY KEY,
    name TEXT,
    price REAL NOT NULL,
    change_pct REAL,
    volume TEXT,
    sector TEXT,
    beta REAL
);

CREATE TABLE clients (
    client_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    ssn TEXT NOT NULL,
    dob TEXT,
    phone TEXT,
    email TEXT,
    net_worth REAL,
    risk_tolerance TEXT NOT NULL,
    account_since TEXT,
    kyc_status TEXT,
    kyc_last_refresh TEXT,
    aml_risk TEXT,
    pep_status INTEGER DEFAULT 0,
    sanctions_check TEXT,
    source_of_funds TEXT,
    documentation TEXT,
    investment_horizon_years INTEGER,
    risk_tolerance_history TEXT  -- JSON list of historical changes
);

CREATE TABLE portfolios (
    portfolio_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    client_name TEXT NOT NULL,
    cash REAL NOT NULL DEFAULT 0
);

CREATE TABLE positions (
    portfolio_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    cost_basis REAL NOT NULL,
    PRIMARY KEY (portfolio_id, ticker),
    FOREIGN KEY (portfolio_id) REFERENCES portfolios(portfolio_id)
);

CREATE TABLE risk_limits (
    portfolio_id TEXT PRIMARY KEY,
    max_position_pct REAL,
    max_sector_pct REAL,
    stop_loss_pct REAL,
    FOREIGN KEY (portfolio_id) REFERENCES portfolios(portfolio_id)
);

CREATE TABLE trades (
    order_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    portfolio_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('BUY','SELL')),
    side TEXT NOT NULL DEFAULT '',
    ticker TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    price REAL NOT NULL,
    commission REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'FILLED',
    submitted_by TEXT,
    blotter_note TEXT,
    call_reference TEXT
);

CREATE TABLE compliance_reports (
    report_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    report_type TEXT NOT NULL,
    submitted_by TEXT,
    content_summary TEXT,
    status TEXT DEFAULT 'PENDING',
    client_id TEXT DEFAULT '',
    client_ids TEXT DEFAULT '',
    portfolio_id TEXT DEFAULT '',
    case_id TEXT DEFAULT '',
    policy_id TEXT DEFAULT '',
    claim_reference TEXT DEFAULT '',
    loan_id TEXT DEFAULT '',
    filed_by_role TEXT DEFAULT ''
);

CREATE TABLE audit_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    tool TEXT NOT NULL DEFAULT '',
    tool_name TEXT NOT NULL DEFAULT '',
    args_json TEXT NOT NULL DEFAULT '{}',
    request_json TEXT NOT NULL DEFAULT '{}',
    request_args TEXT NOT NULL DEFAULT '{}',
    agent_role TEXT NOT NULL DEFAULT '',
    actor_role TEXT NOT NULL DEFAULT '',
    resource_id TEXT NOT NULL DEFAULT '',
    client_id TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL DEFAULT ''
);

-- Extended tables for scaled tasks (products, compliance, enrichment)

CREATE TABLE IF NOT EXISTS loans (
    loan_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    loan_type TEXT,
    origination_date TEXT,
    maturity_date TEXT,
    original_principal REAL,
    current_balance REAL,
    status TEXT,
    interest_rate_pct REAL,
    collateral_account_id TEXT,
    payment_behavior_note TEXT
);

CREATE TABLE IF NOT EXISTS annuity_surrenders (
    surrender_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    associated_portfolio_id TEXT,
    carrier TEXT,
    contract_id TEXT,
    status TEXT,
    request_type TEXT,
    gross_amount REAL,
    surrender_charge_pct REAL,
    estimated_net_proceeds REAL,
    funding_purpose TEXT,
    owner_match_confirmed INTEGER DEFAULT 0,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS aml_alerts (
    alert_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    related_loan_id TEXT,
    alert_type TEXT,
    alert_date TEXT,
    severity TEXT,
    status TEXT DEFAULT 'OPEN',
    trigger_details TEXT,
    initial_recommendation TEXT
);

CREATE TABLE IF NOT EXISTS margin_calls (
    call_id TEXT PRIMARY KEY,
    portfolio_id TEXT NOT NULL,
    client_id TEXT,
    call_time TEXT,
    status TEXT DEFAULT 'OPEN',
    house_requirement_pct REAL,
    current_equity_pct REAL,
    deficiency_amount REAL,
    suggested_liquidation_value REAL,
    trigger TEXT,
    scope_note TEXT
);

CREATE TABLE IF NOT EXISTS death_notifications (
    notification_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    portfolio_id TEXT,
    logged_at TEXT,
    source TEXT,
    status TEXT DEFAULT 'OPEN',
    handling_note TEXT
);

CREATE TABLE IF NOT EXISTS estate_cases (
    case_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    portfolio_id TEXT,
    status TEXT,
    workflow_type TEXT,
    date_opened TEXT,
    documents_received_json TEXT,
    operational_directive TEXT,
    accounts_requiring_hold_json TEXT,
    trading_permitted INTEGER DEFAULT 1,
    trading_reason TEXT
);

CREATE TABLE IF NOT EXISTS compliance_cases (
    case_id TEXT PRIMARY KEY,
    client_id TEXT,
    portfolio_id TEXT,
    case_type TEXT,
    status TEXT,
    priority TEXT,
    opened_date TEXT,
    summary TEXT,
    control_notes TEXT,
    escalation TEXT
);

CREATE TABLE IF NOT EXISTS suitability_profiles (
    client_id TEXT PRIMARY KEY,
    risk_tolerance TEXT,
    liquidity_needs TEXT,
    tax_sensitivity TEXT,
    restricted_actions_json TEXT,
    investment_objective TEXT,
    approved_products_json TEXT
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    portfolio_id TEXT NOT NULL,
    snapshot_timestamp TEXT,
    positions_json TEXT,
    cash REAL,
    snapshot_status TEXT
);
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_percent_like(value: Any, fallback: float | None) -> float | None:
    """Parse numeric / percentage-like values such as ``5`` or ``'5%'``.

    Returns ``fallback`` when the input is blank or cannot be parsed.
    """

    if value is None or value == "":
        return fallback
    try:
        if isinstance(value, str):
            cleaned = value.strip().replace(",", "")
            if cleaned.endswith("%"):
                cleaned = cleaned[:-1].strip()
            return float(cleaned)
        return float(value)
    except (ValueError, TypeError):
        return fallback


# Suitability matrix: risk_tolerance → (approved_asset_classes, restricted_asset_classes)
# Used by tool_check_suitability to dynamically compute suitability based on
# the client's CURRENT risk profile (which may have been downgraded mid-run).
_SUITABILITY_MATRIX: dict[str, tuple[list[str], list[str]]] = {
    "Conservative": (
        ["investment_grade_bonds", "large_cap_equity", "ETFs", "money_market"],
        ["high_yield_bonds", "options", "alternatives", "crypto", "leveraged_etfs", "futures"],
    ),
    "Moderate": (
        ["investment_grade_bonds", "large_cap_equity", "mid_cap_equity", "ETFs", "mutual_funds"],
        ["options_L2_L3", "crypto", "leveraged_etfs", "futures"],
    ),
    "Moderate-Aggressive": (
        ["equities (large/mid cap)", "fixed_income", "ETFs", "mutual_funds", "small_cap_equity"],
        ["options_L3", "crypto", "leveraged_etfs"],
    ),
    "Aggressive": (
        ["all equities", "fixed_income", "ETFs", "options_L1_L3", "alternatives", "crypto"],
        [],
    ),
}


def _suitability_rules(tolerance: str) -> tuple[list[str], list[str]]:
    """Return (approved, restricted) lists for a given risk tolerance.

    Unknown tolerances default to Conservative (most restrictive).
    """
    return _SUITABILITY_MATRIX.get(tolerance, _SUITABILITY_MATRIX["Conservative"])


# ---------------------------------------------------------------------------
# FinanceBank
# ---------------------------------------------------------------------------


class FinanceBank:
    """SQLite-backed stateful backend for finance domain tools."""

    def __init__(self, seed_dir: Path, db_path: str = ":memory:"):
        self.seed_dir = seed_dir
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA_SQL)
        self._seed(seed_dir)
        self._order_counter = 98300
        self._report_counter = 20250900
        self._current_agent_role: str | None = None
        self._active_client_id: str | None = None
        self._active_portfolio_id: str | None = None

    # ------------------------------------------------------------------
    # Seed loading
    # ------------------------------------------------------------------

    def _seed(self, seed_dir: Path) -> None:
        # Prices (with sector + beta for concentration / risk analysis)
        prices_path = seed_dir / "seed_prices.json"
        if prices_path.exists():
            with open(prices_path) as f:
                data = json.load(f)
            for ticker, d in data.items():
                self.conn.execute(
                    "INSERT INTO prices (ticker, name, price, change_pct, volume, sector, beta) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        ticker, d.get("name", ticker), d["price"],
                        d.get("change_pct", 0), d.get("volume", ""),
                        d.get("sector", "unknown"), d.get("beta", 1.0),
                    ),
                )

        # Clients (SSN, risk tolerance, KYC/AML fields)
        clients_path = seed_dir / "seed_clients.json"
        if clients_path.exists():
            with open(clients_path) as f:
                data = json.load(f)
            # Skip extended nested keys and entries without required fields
            for cid, c in data.items():
                if not isinstance(c, dict) or "name" not in c or "ssn" not in c:
                    continue
                self.conn.execute(
                    "INSERT INTO clients (client_id, name, ssn, dob, phone, email, net_worth, "
                    "risk_tolerance, account_since, kyc_status, kyc_last_refresh, aml_risk, "
                    "pep_status, sanctions_check, source_of_funds, documentation, "
                    "investment_horizon_years, risk_tolerance_history) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        cid, c["name"], c["ssn"], c.get("dob"), c.get("phone"),
                        c.get("email"), c.get("net_worth"), c["risk_tolerance"],
                        c.get("account_since"), c.get("kyc_status"),
                        c.get("kyc_last_refresh"), c.get("aml_risk"),
                        1 if c.get("pep_status") else 0,
                        c.get("sanctions_check"), c.get("source_of_funds"),
                        c.get("documentation"), c.get("investment_horizon_years"),
                        json.dumps([{"ts": "seed", "risk_tolerance": c["risk_tolerance"]}]),
                    ),
                )

        # Portfolios + positions
        port_path = seed_dir / "seed_portfolios.json"
        if port_path.exists():
            with open(port_path) as f:
                data = json.load(f)
            for pid, p in data.items():
                if not isinstance(p, dict) or "client_name" not in p:
                    continue  # skip extended keys (trade_history, snapshots_archive, etc.)
                self.conn.execute(
                    "INSERT INTO portfolios (portfolio_id, client_id, client_name, cash) VALUES (?, ?, ?, ?)",
                    (pid, p["client_id"], p["client_name"], p["cash"]),
                )
                for pos in p.get("positions", []):
                    self.conn.execute(
                        "INSERT INTO positions (portfolio_id, ticker, quantity, cost_basis) VALUES (?, ?, ?, ?)",
                        (pid, pos["ticker"], pos["quantity"], pos["cost_basis"]),
                    )

        # Risk limits
        rl_path = seed_dir / "seed_risk_limits.json"
        if rl_path.exists():
            with open(rl_path) as f:
                data = json.load(f)
            for pid, rl in data.items():
                if not isinstance(rl, dict):
                    continue
                self.conn.execute(
                    "INSERT INTO risk_limits (portfolio_id, max_position_pct, max_sector_pct, stop_loss_pct) VALUES (?, ?, ?, ?)",
                    (pid, rl.get("max_position_pct"), rl.get("max_sector_pct"), rl.get("stop_loss_pct")),
                )

        # Extended: suitability profiles from seed_clients.json
        if clients_path.exists():
            with open(clients_path) as f:
                cdata = json.load(f)
            for cid, sp in cdata.get("suitability_profiles", {}).items():
                self.conn.execute(
                    "INSERT OR IGNORE INTO suitability_profiles "
                    "(client_id, risk_tolerance, liquidity_needs, tax_sensitivity, "
                    "restricted_actions_json, investment_objective, approved_products_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (cid, sp.get("risk_tolerance"), sp.get("liquidity_needs"),
                     sp.get("tax_sensitivity"),
                     json.dumps(sp.get("restricted_actions", [])),
                     sp.get("investment_objective"),
                     json.dumps(sp.get("approved_product_types", []))),
                )

        # Extended: trade history + snapshots from seed_portfolios.json
        if port_path.exists():
            with open(port_path) as f:
                pdata = json.load(f)
            for pid, trades in pdata.get("trade_history", {}).items():
                if not isinstance(trades, list):
                    continue
                for t in trades:
                    tid = t.get("trade_id") or f"HIST-{pid}-{t.get('ticker','')}-{t.get('timestamp','')}"
                    action_val = t.get("side", "BUY")
                    self.conn.execute(
                        "INSERT OR IGNORE INTO trades "
                        "(order_id, timestamp, portfolio_id, action, side, ticker, quantity, "
                        "price, commission, status, submitted_by, blotter_note) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'historical', ?)",
                        (tid, t.get("timestamp", ""), pid, action_val,
                         action_val,
                         t.get("ticker", ""), t.get("quantity", 0), t.get("price", 0),
                         t.get("status", "SETTLED"), t.get("memo", t.get("notes", ""))),
                    )
            for sid, snap in pdata.get("snapshots_archive", {}).items():
                self.conn.execute(
                    "INSERT OR IGNORE INTO portfolio_snapshots "
                    "(snapshot_id, portfolio_id, snapshot_timestamp, positions_json, "
                    "cash, snapshot_status) VALUES (?, ?, ?, ?, ?, ?)",
                    (sid, snap.get("portfolio_id", ""), snap.get("snapshot_timestamp", sid),
                     json.dumps(snap.get("positions", [])),
                     snap.get("cash", 0), snap.get("snapshot_status", "")),
                )

        # Products (loans, annuities, AML, margin, death, estate)
        products_path = seed_dir / "seed_products.json"
        if products_path.exists():
            with open(products_path) as f:
                products = json.load(f)
            for lid, ln in products.get("loan_accounts", {}).items():
                self.conn.execute(
                    "INSERT OR IGNORE INTO loans "
                    "(loan_id, client_id, loan_type, origination_date, maturity_date, "
                    "original_principal, current_balance, status, interest_rate_pct, "
                    "collateral_account_id, payment_behavior_note) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (lid, ln.get("client_id"), ln.get("loan_type"),
                     ln.get("origination_date"), ln.get("maturity_date"),
                     ln.get("original_principal"), ln.get("current_balance"),
                     ln.get("status"), ln.get("interest_rate_pct"),
                     ln.get("collateral_account_id"), ln.get("payment_behavior_note")),
                )
            for sid, an in products.get("annuity_surrenders", {}).items():
                self.conn.execute(
                    "INSERT OR IGNORE INTO annuity_surrenders "
                    "(surrender_id, client_id, associated_portfolio_id, carrier, "
                    "contract_id, status, request_type, gross_amount, "
                    "surrender_charge_pct, estimated_net_proceeds, funding_purpose, "
                    "owner_match_confirmed, notes) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (sid, an.get("client_id"), an.get("associated_portfolio_id"),
                     an.get("carrier"), an.get("contract_id"), an.get("status"),
                     an.get("request_type"), an.get("gross_surrender_requested"),
                     an.get("surrender_charge_pct"), an.get("estimated_net_proceeds"),
                     an.get("funding_purpose"),
                     1 if an.get("owner_match_confirmed") else 0, an.get("notes")),
                )
            for aid, al in products.get("aml_alerts", {}).items():
                self.conn.execute(
                    "INSERT OR IGNORE INTO aml_alerts "
                    "(alert_id, client_id, related_loan_id, alert_type, alert_date, "
                    "severity, status, trigger_details, initial_recommendation) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (aid, al.get("client_id"), al.get("related_loan_id"),
                     al.get("alert_type"), al.get("alert_date"), al.get("severity"),
                     al.get("status", "OPEN"), al.get("trigger_details"),
                     al.get("initial_recommendation")),
                )
            for mid, mc in products.get("margin_calls", {}).items():
                self.conn.execute(
                    "INSERT OR IGNORE INTO margin_calls "
                    "(call_id, portfolio_id, client_id, call_time, status, "
                    "house_requirement_pct, current_equity_pct, deficiency_amount, "
                    "suggested_liquidation_value, trigger, scope_note) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (mid, mc.get("portfolio_id"), mc.get("client_id"),
                     mc.get("call_time"), mc.get("status", "OPEN"),
                     mc.get("house_requirement_pct"), mc.get("current_equity_pct"),
                     mc.get("deficiency_amount"), mc.get("suggested_liquidation_value"),
                     mc.get("trigger"), mc.get("scope_note")),
                )
            for nid, dn in products.get("death_notifications", {}).items():
                self.conn.execute(
                    "INSERT OR IGNORE INTO death_notifications "
                    "(notification_id, client_id, portfolio_id, logged_at, source, "
                    "status, handling_note) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (nid, dn.get("client_id"), dn.get("portfolio_id"),
                     dn.get("logged_at"), dn.get("source"),
                     dn.get("status", "OPEN"), dn.get("handling_note")),
                )
            for eid, ec in products.get("estate_workflow", {}).items():
                self.conn.execute(
                    "INSERT OR IGNORE INTO estate_cases "
                    "(case_id, client_id, portfolio_id, status, workflow_type, "
                    "date_opened, documents_received_json, operational_directive, "
                    "accounts_requiring_hold_json, trading_permitted, trading_reason) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (ec.get("case_id", eid), ec.get("client_id"), ec.get("portfolio_id"),
                     ec.get("status"), ec.get("workflow_type"), ec.get("date_opened"),
                     json.dumps(ec.get("documents_received", [])),
                     ec.get("operational_directive"),
                     json.dumps(ec.get("accounts_requiring_hold", [])),
                     0 if ec.get("trade_control", {}).get("trading_permitted") is False else 1,
                     ec.get("trade_control", {}).get("reason", "")),
                )

        # Compliance cases
        compliance_path = seed_dir / "seed_compliance.json"
        if compliance_path.exists():
            with open(compliance_path) as f:
                comp = json.load(f)
            for cid, cc in comp.get("cases", {}).items():
                self.conn.execute(
                    "INSERT OR IGNORE INTO compliance_cases "
                    "(case_id, client_id, portfolio_id, case_type, status, priority, "
                    "opened_date, summary, control_notes, escalation) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (cid, cc.get("client_id"), cc.get("portfolio_id"),
                     cc.get("case_type"), cc.get("status"), cc.get("priority"),
                     cc.get("opened_date"), cc.get("summary"),
                     cc.get("control_notes"), cc.get("escalation")),
                )

        self.conn.commit()

    # ------------------------------------------------------------------
    # Core dispatch
    # ------------------------------------------------------------------

    def set_current_agent(self, role: str | None) -> None:
        """Runner calls this before each tool invocation so audit_log knows who called."""
        self._current_agent_role = role

    def dispatch(self, tool_name: str, args: dict[str, Any]) -> str:
        """Dispatch a tool call: audit + handler lookup + invocation."""
        # 1. Auto-audit
        role_val = self._current_agent_role or ""
        args_str = json.dumps(args, default=str)
        # Extract resource_id from common arg patterns
        resource_id = str(
            args.get("client_id")
            or args.get("portfolio_id")
            or args.get("tickers", "")
        ).strip()
        client_id = str(args.get("client_id", "")).strip()
        try:
            self.conn.execute(
                "INSERT INTO audit_log "
                "(timestamp, tool, tool_name, args_json, request_json, request_args, "
                "agent_role, actor_role, resource_id, client_id, action) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (_now_iso(), tool_name, tool_name,
                 args_str, args_str, args_str,
                 role_val, role_val, resource_id, client_id, tool_name),
            )
            self.conn.commit()
        except Exception:
            pass  # never fail the dispatch on audit error

        # 2. Handler lookup
        handler = getattr(self, f"tool_{tool_name}", None)
        if handler is None:
            return f"[bank: no handler for {tool_name}]"

        # 3. Invoke with lenient kwarg filtering
        try:
            sig = inspect.signature(handler)
            has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
            if has_var_kw:
                return handler(**args)
            # Filter to only accepted kwargs
            valid = {k: v for k, v in args.items() if k in sig.parameters}
            return handler(**valid)
        except TypeError as e:
            return f"[bank: argument error in {tool_name}: {e}]"
        except Exception as e:
            return f"[bank: error in {tool_name}: {type(e).__name__}: {e}]"

    def query_sql(self, query: str) -> list[dict]:
        """Run a read-only SQL query and return rows as list of dicts.

        Only SELECT statements are allowed (guards against accidental
        mutations from completion checkpoints in task YAMLs).
        """
        stripped = query.strip().lstrip("(")
        if not stripped.upper().startswith("SELECT"):
            raise ValueError(f"query_sql only allows SELECT statements, got: {query[:60]}")
        return [dict(row) for row in self.conn.execute(query).fetchall()]

    def dump_to_file(self, path: Path) -> None:
        """Backup the in-memory DB to a file for inspection after the run."""
        path.parent.mkdir(parents=True, exist_ok=True)
        dst = sqlite3.connect(str(path))
        with dst:
            self.conn.backup(dst)
        dst.close()

    def close(self) -> None:
        self.conn.close()

    def apply_task_metadata(self, metadata: dict[str, Any]) -> None:
        """Apply task-specific state mutations after seed loading."""
        metadata = metadata or {}
        client_id = str(metadata.get("client_id") or "").strip()
        portfolio_id = str(metadata.get("portfolio_id") or "").strip()
        self._active_client_id = client_id or None
        self._active_portfolio_id = portfolio_id or None

        seed_trades = metadata.get("seed_trades", [])
        if not seed_trades:
            return

        previous_role = self._current_agent_role
        try:
            for idx, trade in enumerate(seed_trades, start=1):
                self._current_agent_role = trade.get("submitted_by") or "seed_trade"
                result = self.tool_execute_trade(
                    action=trade.get("action", ""),
                    ticker=trade.get("ticker", ""),
                    quantity=trade.get("quantity", 0),
                    portfolio_id=trade.get("portfolio_id", portfolio_id or "PF-4821"),
                    timestamp=trade.get("timestamp"),
                    submitted_by=trade.get("submitted_by"),
                    order_id=trade.get("order_id") or f"SEED-TXN-{idx}",
                    blotter_note=trade.get("blotter_note", ""),
                    call_reference=trade.get("call_reference", ""),
                )
                if not str(result).startswith("Trade Executed Successfully"):
                    raise ValueError(f"Failed to apply seed trade: {result}")
        finally:
            self._current_agent_role = previous_role

    def _resolve_portfolio_id(self, portfolio_id: str = "") -> str:
        resolved = str(portfolio_id or self._active_portfolio_id or "PF-4821").strip()
        return resolved or "PF-4821"

    def _resolve_client_id(self, client_id: str = "", *, portfolio_id: str = "") -> str:
        resolved = str(client_id or self._active_client_id or "").strip()
        if resolved:
            return resolved

        resolved_portfolio_id = self._resolve_portfolio_id(portfolio_id)
        row = self.conn.execute(
            "SELECT client_id FROM portfolios WHERE portfolio_id = ?",
            (resolved_portfolio_id,),
        ).fetchone()
        if row is not None:
            return str(row["client_id"]).strip()
        return ""

    # ==================================================================
    # MUTATING HANDLERS (3)
    # ==================================================================

    def tool_execute_trade(
        self,
        action: str = "",
        ticker: str = "",
        quantity: Any = 0,
        portfolio_id: str = "",
        timestamp: str | None = None,
        submitted_by: str | None = None,
        order_id: str | None = None,
        blotter_note: str = "",
        call_reference: str = "",
        **kwargs: Any,
    ) -> str:
        """Execute a real BUY/SELL trade against portfolio state."""
        action = str(action).upper().strip()
        ticker = str(ticker).upper().strip()
        if action not in ("BUY", "SELL"):
            return f"Trade rejected: unknown action '{action}' (expected BUY or SELL)"
        try:
            qty = int(quantity)
        except (ValueError, TypeError):
            return f"Trade rejected: invalid quantity '{quantity}'"
        if qty <= 0:
            return f"Trade rejected: quantity must be positive, got {qty}"

        portfolio_id = self._resolve_portfolio_id(portfolio_id)

        # Estate freeze check — block trades on frozen portfolios
        freeze = self.conn.execute(
            "SELECT case_id, trading_reason FROM estate_cases "
            "WHERE portfolio_id = ? AND trading_permitted = 0",
            (portfolio_id,),
        ).fetchone()
        if freeze:
            return (
                f"TRADE BLOCKED: Estate freeze active on portfolio {portfolio_id}.\n"
                f"  Case: {freeze['case_id']}\n"
                f"  Reason: {freeze['trading_reason']}"
            )

        price_row = self.conn.execute(
            "SELECT price FROM prices WHERE ticker = ?", (ticker,)
        ).fetchone()
        if not price_row:
            return f"Trade rejected: no market price for {ticker}"
        price = float(price_row["price"])

        port_row = self.conn.execute(
            "SELECT cash FROM portfolios WHERE portfolio_id = ?", (portfolio_id,)
        ).fetchone()
        if not port_row:
            return f"Trade rejected: portfolio {portfolio_id} not found"
        cash = float(port_row["cash"])

        total = qty * price
        commission = round(total * 0.0001, 2)

        if action == "SELL":
            pos = self.conn.execute(
                "SELECT quantity FROM positions WHERE portfolio_id = ? AND ticker = ?",
                (portfolio_id, ticker),
            ).fetchone()
            if not pos:
                return f"Trade rejected: no {ticker} position in {portfolio_id} to sell"
            if int(pos["quantity"]) < qty:
                return (
                    f"Trade rejected: insufficient {ticker} "
                    f"({pos['quantity']} held, {qty} requested)"
                )
            new_qty = int(pos["quantity"]) - qty
            if new_qty == 0:
                self.conn.execute(
                    "DELETE FROM positions WHERE portfolio_id = ? AND ticker = ?",
                    (portfolio_id, ticker),
                )
            else:
                self.conn.execute(
                    "UPDATE positions SET quantity = ? WHERE portfolio_id = ? AND ticker = ?",
                    (new_qty, portfolio_id, ticker),
                )
            self.conn.execute(
                "UPDATE portfolios SET cash = cash + ? WHERE portfolio_id = ?",
                (total - commission, portfolio_id),
            )

        else:  # BUY
            needed = total + commission
            if cash < needed:
                return (
                    f"Trade rejected: insufficient cash "
                    f"(have ${cash:,.2f}, need ${needed:,.2f})"
                )
            existing = self.conn.execute(
                "SELECT quantity, cost_basis FROM positions WHERE portfolio_id = ? AND ticker = ?",
                (portfolio_id, ticker),
            ).fetchone()
            if existing:
                old_qty = int(existing["quantity"])
                old_cost = float(existing["cost_basis"])
                new_qty = old_qty + qty
                new_cost = (old_qty * old_cost + total) / new_qty
                self.conn.execute(
                    "UPDATE positions SET quantity = ?, cost_basis = ? WHERE portfolio_id = ? AND ticker = ?",
                    (new_qty, new_cost, portfolio_id, ticker),
                )
            else:
                self.conn.execute(
                    "INSERT INTO positions (portfolio_id, ticker, quantity, cost_basis) VALUES (?, ?, ?, ?)",
                    (portfolio_id, ticker, qty, price),
                )
            self.conn.execute(
                "UPDATE portfolios SET cash = cash - ? WHERE portfolio_id = ?",
                (needed, portfolio_id),
            )

        if order_id is None:
            self._order_counter += 1
            order_id = f"TXN-{self._order_counter}"
        self.conn.execute(
            "INSERT INTO trades (order_id, timestamp, portfolio_id, action, side, ticker, quantity, price, commission, status, submitted_by, blotter_note, call_reference) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'FILLED', ?, ?, ?)",
            (
                order_id,
                timestamp or _now_iso(),
                portfolio_id,
                action,
                action,
                ticker,
                qty,
                price,
                commission,
                submitted_by or self._current_agent_role,
                blotter_note or None,
                call_reference or None,
            ),
        )
        self.conn.commit()

        return (
            f"Trade Executed Successfully:\n"
            f"  Order ID: {order_id}\n"
            f"  Action: {action} {qty} {ticker} @ ${price:.2f}\n"
            f"  Total: ${total:,.2f}\n"
            f"  Commission: ${commission:.2f}\n"
            f"  Status: FILLED\n"
            f"  Portfolio: {portfolio_id}\n"
            f"  Settlement: T+1"
        )

    def tool_set_risk_limits(
        self,
        max_position_pct: Any = None,
        max_sector_pct: Any = None,
        stop_loss_pct: Any = None,
        portfolio_id: str = "",
        client_id: str = "",
        risk_tolerance: str = "",
        **kwargs: Any,
    ) -> str:
        """Update risk limits for a portfolio.

        If client_id + risk_tolerance are provided (or if client_id is omitted
        but risk_tolerance is given, in which case we look up the client from
        the portfolio), ALSO update the client's declared risk_tolerance and
        append to their risk_tolerance_history. This is how fin-t4
        (risk profile downgrade) achieves state closure: set_risk_limits
        propagates the new profile to the clients table, so subsequent
        check_suitability calls reflect the change.
        """
        portfolio_id = self._resolve_portfolio_id(portfolio_id)

        row = self.conn.execute(
            "SELECT max_position_pct, max_sector_pct, stop_loss_pct FROM risk_limits WHERE portfolio_id = ?",
            (portfolio_id,),
        ).fetchone()
        if not row:
            return f"Risk limits not found for portfolio {portfolio_id}"

        old_pos = row["max_position_pct"]
        old_sec = row["max_sector_pct"]
        old_stop = row["stop_loss_pct"]
        new_pos = _parse_percent_like(max_position_pct, old_pos)
        new_sec = _parse_percent_like(max_sector_pct, old_sec)
        new_stop = _parse_percent_like(stop_loss_pct, old_stop)

        self.conn.execute(
            "UPDATE risk_limits SET max_position_pct = ?, max_sector_pct = ?, stop_loss_pct = ? WHERE portfolio_id = ?",
            (new_pos, new_sec, new_stop, portfolio_id),
        )

        # Optional client risk tolerance propagation
        client_line = ""
        if risk_tolerance:
            # Resolve client_id from portfolio if not given
            resolved_client_id = self._resolve_client_id(client_id, portfolio_id=portfolio_id)

            if resolved_client_id:
                c_row = self.conn.execute(
                    "SELECT risk_tolerance, risk_tolerance_history FROM clients WHERE client_id = ?",
                    (resolved_client_id,),
                ).fetchone()
                if c_row:
                    old_tol = c_row["risk_tolerance"]
                    try:
                        history = json.loads(c_row["risk_tolerance_history"] or "[]")
                    except (json.JSONDecodeError, TypeError):
                        history = []
                    history.append({
                        "ts": _now_iso(),
                        "risk_tolerance": risk_tolerance,
                        "changed_by": self._current_agent_role or "system",
                        "previous": old_tol,
                    })
                    self.conn.execute(
                        "UPDATE clients SET risk_tolerance = ?, risk_tolerance_history = ? WHERE client_id = ?",
                        (risk_tolerance, json.dumps(history), resolved_client_id),
                    )
                    client_line = (
                        f"\n  Client Risk Tolerance ({resolved_client_id}): "
                        f"{old_tol} → {risk_tolerance}"
                    )

        self.conn.commit()

        return (
            f"Risk Limits Updated — {portfolio_id}:\n"
            f"  Max Single Position: {old_pos}% → {new_pos}%\n"
            f"  Max Sector Concentration: {old_sec}% → {new_sec}%\n"
            f"  Stop-Loss Threshold: {old_stop}% → {new_stop}%"
            f"{client_line}\n"
            f"  Updated by: {self._current_agent_role or 'system'}\n"
            f"  Effective: immediately"
        )

    def tool_submit_compliance_report(
        self,
        report_type: str = "general",
        content: str = "",
        **kwargs: Any,
    ) -> str:
        """Submit a compliance report — returns a unique report ID."""
        self._report_counter += 1
        report_id = f"CR-{self._report_counter}"
        summary = (content or "").strip()

        # Extract structured fields from kwargs for checkpoint SQL queries
        client_id = str(kwargs.get("client_id", "")).strip()
        client_ids = str(kwargs.get("client_ids", "")).strip()
        portfolio_id = str(kwargs.get("portfolio_id", "")).strip()
        case_id = str(kwargs.get("case_id", "")).strip()
        policy_id = str(kwargs.get("policy_id", "")).strip()
        claim_reference = str(kwargs.get("claim_reference", "")).strip()
        loan_id = str(kwargs.get("loan_id", "")).strip()
        filed_by_role = self._current_agent_role or ""

        # Auto-extract IDs from content if not explicitly provided
        if not client_id:
            client_id = str(self._active_client_id or "").strip()
        if not portfolio_id:
            portfolio_id = str(self._active_portfolio_id or "").strip()

        self.conn.execute(
            "INSERT INTO compliance_reports "
            "(report_id, timestamp, report_type, submitted_by, content_summary, status, "
            "client_id, client_ids, portfolio_id, case_id, policy_id, claim_reference, loan_id, filed_by_role) "
            "VALUES (?, ?, ?, ?, ?, 'PENDING', ?, ?, ?, ?, ?, ?, ?, ?)",
            (report_id, _now_iso(), report_type, self._current_agent_role, summary,
             client_id, client_ids, portfolio_id, case_id, policy_id, claim_reference, loan_id, filed_by_role),
        )
        self.conn.commit()
        return (
            f"Compliance Report Submitted:\n"
            f"  Report ID: {report_id}\n"
            f"  Type: {report_type}\n"
            f"  Submitted By: {self._current_agent_role or 'unknown'}\n"
            f"  Status: Pending Review\n"
            f"  Expected Response: 3 business days"
        )

    # ==================================================================
    # STATE-AWARE READS (8)
    # ==================================================================

    def tool_get_client_profile(self, client_id: str = "", **kwargs: Any) -> str:
        """Return full client profile from the clients table."""
        client_id = self._resolve_client_id(client_id)
        row = self.conn.execute(
            "SELECT client_id, name, ssn, dob, phone, email, net_worth, risk_tolerance, "
            "account_since, kyc_status, aml_risk, source_of_funds "
            "FROM clients WHERE client_id = ?",
            (client_id,),
        ).fetchone()
        if not row:
            return f"Client not found: {client_id}"
        # Render net_worth with exact cents for stable pattern matching.
        net_worth_exact = f"{row['net_worth']:.2f}"
        lines = [
            f"Client Profile:",
            f"  Client ID: {row['client_id']}",
            f"  Name: {row['name']}",
            f"  SSN: {row['ssn']}",
            f"  DOB: {row['dob']}",
            f"  Phone: {row['phone']}",
            f"  Email: {row['email']}",
            f"  Net Worth (exact): ${net_worth_exact}",
            f"  Risk Tolerance: {row['risk_tolerance']}",
            f"  Account Since: {row['account_since']}",
            f"  KYC Status: {row['kyc_status']}",
            f"  AML Risk: {row['aml_risk']}",
        ]

        # Active flags from extended tables
        flags: list[str] = []
        for aml in self.conn.execute(
            "SELECT alert_id, alert_type, severity FROM aml_alerts "
            "WHERE client_id = ? AND status = 'OPEN'", (client_id,),
        ).fetchall():
            flags.append(f"AML ALERT {aml['alert_id']}: {aml['alert_type']} ({aml['severity']})")
        for dn in self.conn.execute(
            "SELECT notification_id, status FROM death_notifications "
            "WHERE client_id = ? AND status = 'OPEN'", (client_id,),
        ).fetchall():
            flags.append(f"DEATH NOTIFICATION {dn['notification_id']}: {dn['status']}")
        for ec in self.conn.execute(
            "SELECT case_id, workflow_type, status FROM estate_cases "
            "WHERE client_id = ? AND status NOT IN ('SUPERSEDED_DO_NOT_USE','NO_ACTION','CLOSED')",
            (client_id,),
        ).fetchall():
            flags.append(f"ESTATE CASE {ec['case_id']}: {ec['workflow_type']} ({ec['status']})")
        for ln in self.conn.execute(
            "SELECT loan_id, loan_type, current_balance, status FROM loans "
            "WHERE client_id = ?", (client_id,),
        ).fetchall():
            flags.append(f"LOAN {ln['loan_id']}: {ln['loan_type']} (balance=${ln['current_balance']:,.2f}, {ln['status']})")

        if flags:
            lines.append("  --- Active Flags ---")
            for f in flags:
                lines.append(f"  ⚠ {f}")

        return "\n".join(lines)

    def tool_check_suitability(
        self, client_id: str = "", asset_class: str = "", **kwargs: Any
    ) -> str:
        """Check suitability of an asset class against the client's CURRENT risk profile.

        Dynamic: reads clients.risk_tolerance (which may have been updated by
        set_risk_limits in the same run) and the risk_tolerance_history.
        If no asset_class is given, returns the full suitability profile.
        """
        client_id = self._resolve_client_id(client_id)
        row = self.conn.execute(
            "SELECT client_id, name, risk_tolerance, risk_tolerance_history, "
            "investment_horizon_years, kyc_status "
            "FROM clients WHERE client_id = ?",
            (client_id,),
        ).fetchone()
        if not row:
            return f"Client not found: {client_id}"

        tolerance = row["risk_tolerance"]
        approved, restricted = _suitability_rules(tolerance)

        # Parse history to flag recent changes
        try:
            history = json.loads(row["risk_tolerance_history"] or "[]")
        except (json.JSONDecodeError, TypeError):
            history = []
        downgraded = len(history) > 1

        lines = [
            f"Suitability Check — {row['client_id']} ({row['name']}):",
            f"  Current Risk Tolerance: {tolerance}"
            + (" (RECENTLY UPDATED)" if downgraded else ""),
            f"  Investment Horizon: {row['investment_horizon_years']} years",
            f"  KYC Status: {row['kyc_status']}",
            f"  Approved Asset Classes: {', '.join(approved)}",
            f"  Restricted: {', '.join(restricted) if restricted else 'none'}",
        ]

        # Enriched suitability from suitability_profiles table (if available)
        sp = self.conn.execute(
            "SELECT liquidity_needs, tax_sensitivity, restricted_actions_json, "
            "investment_objective, approved_products_json "
            "FROM suitability_profiles WHERE client_id = ?",
            (client_id,),
        ).fetchone()
        if sp:
            lines.append(f"  Liquidity Needs: {sp['liquidity_needs']}")
            lines.append(f"  Tax Sensitivity: {sp['tax_sensitivity']}")
            lines.append(f"  Investment Objective: {sp['investment_objective']}")
            try:
                restricted = json.loads(sp["restricted_actions_json"] or "[]")
                if restricted:
                    lines.append(f"  Restricted Actions: {', '.join(restricted)}")
            except (json.JSONDecodeError, TypeError):
                pass

        if asset_class:
            verdict = "SUITABLE" if asset_class.lower() in [a.lower() for a in approved] else "NOT SUITABLE"
            lines.append(f"  Verdict for '{asset_class}': {verdict}")

        if downgraded:
            lines.append(f"  Profile History: {history}")
        return "\n".join(lines)

    def tool_get_portfolio_summary(
        self, portfolio_id: str = "", **kwargs: Any
    ) -> str:
        """Return current portfolio state with sector concentration and limit check.

        Reflects all state changes in this run (trades, risk_limits updates).
        Used by risk_officer for concentration checks, PM for overview, etc.
        """
        portfolio_id = self._resolve_portfolio_id(portfolio_id)
        port = self.conn.execute(
            "SELECT client_id, client_name, cash FROM portfolios WHERE portfolio_id = ?",
            (portfolio_id,),
        ).fetchone()
        if not port:
            return f"Portfolio {portfolio_id} not found."

        rows = self.conn.execute(
            "SELECT p.ticker, p.quantity, p.cost_basis, pr.price, pr.sector, pr.beta "
            "FROM positions p LEFT JOIN prices pr ON p.ticker = pr.ticker "
            "WHERE p.portfolio_id = ? ORDER BY p.ticker",
            (portfolio_id,),
        ).fetchall()

        cash = float(port["cash"])
        total = cash
        sector_values: dict[str, float] = {}
        position_lines: list[str] = []
        for r in rows:
            qty = int(r["quantity"])
            cost = float(r["cost_basis"])
            price = float(r["price"]) if r["price"] is not None else 0.0
            sector = r["sector"] or "unknown"
            value = qty * price
            total += value
            sector_values[sector] = sector_values.get(sector, 0.0) + value
            position_lines.append(
                f"  {qty} {r['ticker']} @ ${price:.2f} "
                f"(sector={sector}, cost=${cost:.2f}) = ${value:,.2f}"
            )

        # Load risk limits for breach check
        lim = self.conn.execute(
            "SELECT max_position_pct, max_sector_pct FROM risk_limits WHERE portfolio_id = ?",
            (portfolio_id,),
        ).fetchone()
        pos_lim = float(lim["max_position_pct"]) if lim and lim["max_position_pct"] else 100.0
        sec_lim = float(lim["max_sector_pct"]) if lim and lim["max_sector_pct"] else 100.0

        # Compute concentration
        max_pos_pct = 0.0
        for r in rows:
            qty = int(r["quantity"])
            price = float(r["price"]) if r["price"] is not None else 0.0
            if total > 0:
                pct = qty * price / total * 100
                max_pos_pct = max(max_pos_pct, pct)

        lines = [f"Portfolio {portfolio_id} ({port['client_name']}):"]
        lines.extend(position_lines)
        lines.append(f"  Cash: ${cash:,.2f}")
        lines.append(f"  Total AUM: ${total:,.2f}")
        lines.append("  --- Concentration ---")
        for sector, val in sorted(sector_values.items(), key=lambda kv: -kv[1]):
            pct = val / total * 100 if total > 0 else 0
            flag = " ⚠ BREACH" if pct > sec_lim else ""
            lines.append(f"  {sector}: ${val:,.2f} ({pct:.1f}% of AUM, limit {sec_lim}%){flag}")
        lines.append(
            f"  Max Single Position: {max_pos_pct:.1f}% (limit {pos_lim}%)"
            + (" ⚠ BREACH" if max_pos_pct > pos_lim else "")
        )

        # Risk warnings from extended tables
        warnings: list[str] = []
        for mc in self.conn.execute(
            "SELECT call_id, deficiency_amount, trigger FROM margin_calls "
            "WHERE portfolio_id = ? AND status = 'OPEN'", (portfolio_id,),
        ).fetchall():
            warnings.append(
                f"MARGIN CALL {mc['call_id']}: deficiency ${mc['deficiency_amount']:,.2f} — {mc['trigger']}"
            )
        for ec in self.conn.execute(
            "SELECT case_id, trading_permitted, trading_reason FROM estate_cases "
            "WHERE portfolio_id = ?", (portfolio_id,),
        ).fetchall():
            if not ec["trading_permitted"]:
                warnings.append(f"ESTATE FREEZE {ec['case_id']}: trading blocked — {ec['trading_reason']}")
        for cc in self.conn.execute(
            "SELECT case_id, case_type, status FROM compliance_cases "
            "WHERE portfolio_id = ? AND status != 'CLOSED'", (portfolio_id,),
        ).fetchall():
            warnings.append(f"COMPLIANCE HOLD {cc['case_id']}: {cc['case_type']} ({cc['status']})")

        if warnings:
            lines.append("  --- Risk Warnings ---")
            for w in warnings:
                lines.append(f"  ⚠ {w}")

        return "\n".join(lines)

    def tool_get_trade_history(
        self, portfolio_id: str = "", days: Any = 30, **kwargs: Any
    ) -> str:
        """Return actual trade history for this portfolio from the trades table."""
        portfolio_id = self._resolve_portfolio_id(portfolio_id)
        rows = self.conn.execute(
            "SELECT order_id, timestamp, action, ticker, quantity, price, commission, status, submitted_by, blotter_note, call_reference "
            "FROM trades WHERE portfolio_id = ? ORDER BY timestamp",
            (portfolio_id,),
        ).fetchall()
        if not rows:
            return f"Trade History — {portfolio_id}: (no trades this run)"
        lines = [f"Trade History — {portfolio_id} ({len(rows)} trades this run):"]
        for r in rows:
            who = r["submitted_by"] or "?"
            note = r["blotter_note"] or ""
            call_ref = r["call_reference"] or ""
            extras: list[str] = []
            if note:
                extras.append(f'note="{note}"')
            if call_ref:
                extras.append(f"call_ref={call_ref}")
            suffix = f" [{' ; '.join(extras)}]" if extras else ""
            lines.append(
                f"  [{r['timestamp']}] {r['action']} {r['quantity']} {r['ticker']} @ ${r['price']:.2f} "
                f"(Order {r['order_id']}, by {who}, {r['status']}){suffix}"
            )
        return "\n".join(lines)

    # ==================================================================
    # MARKET DATA (bank-backed lookup from prices table)
    # ==================================================================

    def tool_get_market_data(self, tickers: str = "", **kwargs: Any) -> str:
        """Return live market data (price, change, volume, sector, beta) from the prices table."""
        keys = [k.strip().upper() for k in str(tickers).split(",") if k.strip()]
        if not keys:
            keys = [k["ticker"] for k in self.conn.execute("SELECT ticker FROM prices").fetchall()]
        lines = ["Market Data (live):"]
        for k in keys:
            row = self.conn.execute(
                "SELECT name, price, change_pct, volume, sector, beta FROM prices WHERE ticker = ?",
                (k,),
            ).fetchone()
            if row:
                lines.append(
                    f"  {k} ({row['name']}): ${row['price']:.2f} "
                    f"({row['change_pct']:+.1f}%), Vol: {row['volume']}, "
                    f"sector={row['sector']}, beta={row['beta']:.2f}"
                )
            else:
                lines.append(f"  {k}: not in market data")
        return "\n".join(lines)
