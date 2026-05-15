"""DailyLifeBank — SQLite-backed stateful backend for daily-life tasks.

Expanded for scaled tasks. Loads the daily-life fixture files and materializes
all searchable service candidates into service_plans. Schema includes every
column referenced by sql_assert completion checkpoints.
"""

from __future__ import annotations

import inspect
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from masp.banks.audit import (
    ToolWriteBackfill,
    action_args,
    action_role,
    backfill_write_roles,
    json_args,
    reconcile_audit_log,
)
from masp.banks.common import (
    filter_rows_by_query,
    normalize_query_text as _norm,
    parse_csv_values as _parse_csv,
    stringify as _s,
)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE customers (
    customer_id TEXT PRIMARY KEY,
    full_name TEXT NOT NULL DEFAULT '',
    preferred_city TEXT NOT NULL DEFAULT '',
    default_payment_ref TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE requests (
    request_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL DEFAULT '',
    family TEXT NOT NULL DEFAULT '',
    source_benchmark TEXT NOT NULL DEFAULT '',
    objective TEXT NOT NULL DEFAULT '',
    budget_cap REAL NOT NULL DEFAULT 0,
    preferred_window TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    payment_status TEXT NOT NULL DEFAULT 'pending',
    selected_plan_id TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE customer_contexts (
    request_id TEXT NOT NULL DEFAULT '',
    customer_id TEXT NOT NULL DEFAULT '',
    context_scope TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (request_id, context_scope)
);

CREATE TABLE service_plans (
    plan_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL DEFAULT '',
    service_type TEXT NOT NULL DEFAULT '',
    provider_id TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    price REAL NOT NULL DEFAULT 0,
    schedule TEXT NOT NULL DEFAULT '',
    eligibility TEXT NOT NULL DEFAULT '',
    details TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE coverage_checks (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT '',
    request_id TEXT NOT NULL DEFAULT '',
    plan_id TEXT NOT NULL DEFAULT '',
    option_id TEXT NOT NULL DEFAULT '',
    bundle_id TEXT NOT NULL DEFAULT '',
    option_ref TEXT NOT NULL DEFAULT '',
    coverage_id TEXT NOT NULL DEFAULT '',
    budget_record_id TEXT NOT NULL DEFAULT '',
    policy_id TEXT NOT NULL DEFAULT '',
    coverage_scope TEXT NOT NULL DEFAULT '',
    decision TEXT NOT NULL DEFAULT '',
    estimated_out_of_pocket REAL NOT NULL DEFAULT 0,
    max_approved REAL NOT NULL DEFAULT 0,
    summary TEXT NOT NULL DEFAULT '',
    authored_by TEXT NOT NULL DEFAULT ''
);

CREATE TABLE service_submissions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL DEFAULT '',
    request_id TEXT NOT NULL DEFAULT '',
    customer_id TEXT NOT NULL DEFAULT '',
    plan_id TEXT NOT NULL DEFAULT '',
    option_id TEXT NOT NULL DEFAULT '',
    bundle_id TEXT NOT NULL DEFAULT '',
    selected_option_id TEXT NOT NULL DEFAULT '',
    plan_ref TEXT NOT NULL DEFAULT '',
    submission_id TEXT NOT NULL DEFAULT '',
    template_id TEXT NOT NULL DEFAULT '',
    provider_id TEXT NOT NULL DEFAULT '',
    submission_type TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    authored_by TEXT NOT NULL DEFAULT '',
    authorized_amount REAL NOT NULL DEFAULT 0,
    scheduled_time TEXT NOT NULL DEFAULT '',
    linked_authorization TEXT NOT NULL DEFAULT '',
    schedule TEXT NOT NULL DEFAULT ''
);

CREATE TABLE payment_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL DEFAULT '',
    request_id TEXT NOT NULL DEFAULT '',
    customer_id TEXT NOT NULL DEFAULT '',
    plan_id TEXT NOT NULL DEFAULT '',
    option_id TEXT NOT NULL DEFAULT '',
    bundle_id TEXT NOT NULL DEFAULT '',
    selected_option TEXT NOT NULL DEFAULT '',
    coverage_id TEXT NOT NULL DEFAULT '',
    policy_id TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL DEFAULT '',
    amount REAL NOT NULL DEFAULT 0,
    amount_usd REAL NOT NULL DEFAULT 0,
    amount_limit REAL NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'USD',
    payment_ref TEXT NOT NULL DEFAULT '',
    payment_path TEXT NOT NULL DEFAULT '',
    payment_reference TEXT NOT NULL DEFAULT '',
    authorization_id TEXT NOT NULL DEFAULT '',
    authorization_note_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    authored_by TEXT NOT NULL DEFAULT ''
);

CREATE TABLE provider_threads (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT '',
    request_id TEXT NOT NULL DEFAULT '',
    provider_id TEXT NOT NULL DEFAULT '',
    actor_role TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT '',
    message_type TEXT NOT NULL DEFAULT '',
    purpose TEXT NOT NULL DEFAULT '',
    bundle_id TEXT NOT NULL DEFAULT '',
    option_id TEXT NOT NULL DEFAULT '',
    template_id TEXT NOT NULL DEFAULT '',
    message_id TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    authored_by TEXT NOT NULL DEFAULT ''
);

CREATE TABLE audit_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT '',
    tool TEXT NOT NULL DEFAULT '',
    tool_name TEXT NOT NULL DEFAULT '',
    args_json TEXT NOT NULL DEFAULT '{}',
    agent_role TEXT NOT NULL DEFAULT '',
    actor_role TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT '',
    customer_id TEXT NOT NULL DEFAULT '',
    request_id TEXT NOT NULL DEFAULT '',
    resource_id TEXT NOT NULL DEFAULT '',
    attachment_id TEXT NOT NULL DEFAULT '',
    target_id TEXT NOT NULL DEFAULT '',
    bundle_id TEXT NOT NULL DEFAULT '',
    option_id TEXT NOT NULL DEFAULT '',
    provider_record_id TEXT NOT NULL DEFAULT '',
    action_outcome TEXT NOT NULL DEFAULT ''
);

CREATE TABLE contracts (
    contract_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL DEFAULT '',
    customer_id TEXT NOT NULL DEFAULT '',
    provider_id TEXT NOT NULL DEFAULT '',
    provider_name TEXT NOT NULL DEFAULT '',
    membership_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    term_start TEXT NOT NULL DEFAULT '',
    term_end TEXT NOT NULL DEFAULT '',
    monthly_rate_usd REAL NOT NULL DEFAULT 0,
    autopay_ref TEXT NOT NULL DEFAULT '',
    cancellation_policy_summary TEXT NOT NULL DEFAULT '',
    billing_cycle_day INTEGER NOT NULL DEFAULT 1,
    member_account_ref TEXT NOT NULL DEFAULT ''
);

CREATE TABLE providers (
    provider_id TEXT PRIMARY KEY,
    provider_name TEXT NOT NULL DEFAULT '',
    related_option_id TEXT NOT NULL DEFAULT '',
    review_notes TEXT NOT NULL DEFAULT '',
    message_guidance TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{}'
);
"""

_SEED_TS = "2000-01-01T00:00:00Z"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_money(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value or "").strip().replace("$", "").replace(",", "")
    if not cleaned:
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _f(val: Any, default: float = 0.0) -> float:
    """Coerce to float."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _json_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    return _s(value)


def _first_text(mapping: dict, *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return _json_text(value)
    return ""


def _first_money(mapping: dict, *keys: str) -> float:
    for key in keys:
        value = mapping.get(key)
        parsed = _parse_money(value)
        if parsed:
            return parsed
    return 0.0


def _status_is_current(status: Any) -> bool:
    text = _norm(status)
    stale_tokens = (
        "archived", "closed", "expired", "inactive", "legacy", "old",
        "outdated", "prev", "prior", "stale", "superseded", "voided",
    )
    return not any(token in text for token in stale_tokens)


def _request_budget_cap(req: dict) -> float:
    direct = _first_money(
        req,
        "budget_cap",
        "spend_cap_usd",
        "budget_cap_usd",
        "not_to_exceed_total",
        "approved_cap_usd",
        "payment_cap_usd",
        "weekly_spend_cap_usd",
        "monthly_cap_usd",
        "monthly_rent_cap_usd",
        "reimbursement_cap_usd",
        "max_out_of_pocket_first_visit_usd",
    )
    if direct:
        return direct
    for key in ("budget_scope", "constraints", "budget_context"):
        nested = req.get(key)
        if isinstance(nested, dict):
            nested_cap = _first_money(
                nested,
                "not_to_exceed_total",
                "budget_cap",
                "budget_cap_usd",
                "spend_cap_usd",
                "approved_cap_usd",
                "payment_cap_usd",
                "weekly_spend_cap_usd",
                "monthly_cap_usd",
                "monthly_rent_cap_usd",
                "reimbursement_cap_usd",
                "max_out_of_pocket_first_visit_usd",
            )
            if nested_cap:
                return nested_cap
    return 0.0


# ---------------------------------------------------------------------------
# Bank
# ---------------------------------------------------------------------------

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

    # -----------------------------------------------------------------------
    # Seed loading — three-phase
    # -----------------------------------------------------------------------

    def _seed(self, seed_dir: Path) -> None:
        self._seed_requests(seed_dir)
        svc_path = seed_dir / "seed_services.json"
        if svc_path.exists():
            self._seed_services(seed_dir)
        opt_path = seed_dir / "seed_options.json"
        if opt_path.exists():
            self._seed_options(seed_dir)
        rec_path = seed_dir / "records.json"
        if rec_path.exists():
            self._seed_records(seed_dir)
        self._materialize_contracts_as_plans()
        self.conn.commit()

    # -- Phase 1: seed_requests.json ----------------------------------------

    def _seed_requests(self, seed_dir: Path) -> None:
        with open(seed_dir / "seed_requests.json") as f:
            seed = json.load(f)

        for cid, p in seed.get("customers", {}).items():
            self._upsert_customer(cid, p)

        for rid, p in seed.get("requests", {}).items():
            self._upsert_request(rid, p)
            for scope, content in p.get("contexts", {}).items():
                self._upsert_context(rid, p.get("customer_id", ""), scope, content)
            for pid, plan in p.get("plans", {}).items():
                self._upsert_plan(pid, rid, plan)
            for pid, cov in p.get("coverage_checks", {}).items():
                self._insert_coverage_seed(rid, pid, cov)

        # extended_customer_contexts
        for rid, ctx_data in seed.get("extended_customer_contexts", {}).items():
            cid = _s(ctx_data.get("customer_id", ""))
            for scope_key in ("profile", "budget", "constraints", "timing",
                              "payment_scope", "medical_notes", "address_and_access",
                              "meal_constraints", "pet_summary"):
                val = ctx_data.get(scope_key)
                if val is not None:
                    content = json.dumps(val, default=str) if isinstance(val, dict) else _s(val)
                    self._upsert_context(rid, cid, scope_key, content)

        # extended_coverage_checks
        for key, cov in seed.get("extended_coverage_checks", {}).items():
            rid = _s(cov.get("request_id", key.split("/")[0] if "/" in key else key))
            pid = _s(cov.get("plan_id", key))
            self._insert_coverage_seed(rid, pid, cov)

        # extended_service_plans
        for pid, plan in seed.get("extended_service_plans", {}).items():
            rid = _s(plan.get("request_id", ""))
            self._upsert_plan(pid, rid, plan)

    # -- Phase 2: seed_services.json ----------------------------------------

    def _seed_services(self, seed_dir: Path) -> None:
        with open(seed_dir / "seed_services.json") as f:
            svc = json.load(f)

        # contracts
        for cid, c in svc.get("contracts", {}).items():
            self.conn.execute(
                "INSERT OR IGNORE INTO contracts "
                "(contract_id, request_id, customer_id, provider_id, provider_name, "
                "membership_name, status, term_start, term_end, monthly_rate_usd, "
                "autopay_ref, cancellation_policy_summary, billing_cycle_day, "
                "member_account_ref) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, _s(c.get("request_id")), _s(c.get("customer_id")), _s(c.get("provider_id")),
                 _s(c.get("provider_name")), _s(c.get("membership_name")),
                 _s(c.get("status")), _s(c.get("term_start")), _s(c.get("term_end")),
                 _f(c.get("monthly_rate_usd")), _s(c.get("autopay_ref")),
                 _s(c.get("cancellation_policy_summary")),
                 int(c.get("billing_cycle_day", 1)),
                 _s(c.get("member_account_ref"))),
            )

        # service_requests → requests table (e.g., DL-012)
        for rid, req in svc.get("service_requests", {}).items():
            if isinstance(req, dict):
                self._seed_record_request(rid, req)

        # Additional option collections introduced by later daily-life tasks.
        for collection in ("wellness_program_options", "rental_bundle_options",
                           "service_listings"):
            for pid, plan in svc.get(collection, {}).items():
                if isinstance(plan, dict):
                    self._upsert_plan(pid, _s(plan.get("request_id", "")), plan)

        # providers (top-level, excluding nested sub-dict)
        for pid, p in svc.get("providers", {}).items():
            if pid == "providers":
                # Nested sub-dict contains attachments, not providers
                for sub_id, sub in p.items():
                    self._seed_attachment_or_context(sub_id, sub)
                continue
            if not isinstance(p, dict):
                continue
            self._upsert_provider(pid, p)

        # payment_authorizations → seed coverage_checks as reference
        for auth_id, auth in svc.get("payment_authorizations", {}).items():
            rid = _s(auth.get("request_id", ""))
            opt = _s(auth.get("selected_option", ""))
            self._insert_coverage_seed(rid, opt, {
                "coverage_scope": "payment_authorization",
                "decision": _s(auth.get("authorization_status", "approved")),
                "summary": _s(auth.get("payment_path", "")),
                "coverage_id": auth_id,
            })

        # provider_messages → provider_threads
        for mid, msg in svc.get("provider_messages", {}).items():
            if isinstance(msg, dict):
                self._insert_provider_thread_seed(mid, msg)

        # provider_threads → provider_threads
        for tid, thread in svc.get("provider_threads", {}).items():
            if isinstance(thread, dict):
                self._insert_provider_thread_seed(tid, thread)

        # multimodal_records → customer_contexts
        for mid, rec in svc.get("multimodal_records", {}).items():
            rid = _s(rec.get("request_id", ""))
            scope = f"evidence:{_s(rec.get('media_type', rec.get('type', 'document')))}"
            content = _s(rec.get("extracted_text", rec.get("content", json.dumps(rec, default=str))))
            self._upsert_context(rid, "", scope, f"[{mid}] {content}")

        # budget_records → coverage_checks
        for bid, brec in svc.get("budget_records", {}).items():
            if isinstance(brec, dict) and "request_id" not in brec:
                # Might be a nested collection
                for sub_id, sub in brec.items():
                    if isinstance(sub, dict):
                        rid = _s(sub.get("request_id", ""))
                        self._insert_coverage_seed(rid, sub_id, {
                            "coverage_scope": "budget",
                            "decision": _s(sub.get("decision", "")),
                            "summary": json.dumps(sub, default=str),
                            "budget_record_id": sub_id,
                            "max_approved": _f(sub.get("approved_cap_usd",
                                                       sub.get("deposit_limit_usd", 0))),
                        })
            elif isinstance(brec, dict):
                rid = _s(brec.get("request_id", ""))
                self._insert_coverage_seed(rid, bid, {
                    "coverage_scope": "budget",
                    "decision": _s(brec.get("decision", "")),
                    "summary": json.dumps(brec, default=str),
                    "budget_record_id": bid,
                    "max_approved": _f(brec.get("approved_cap_usd",
                                                brec.get("deposit_limit_usd", 0))),
                })

        # coverage_records → coverage_checks
        for cov_id, cov in svc.get("coverage_records", {}).items():
            if isinstance(cov, dict):
                rid = _s(cov.get("request_id", ""))
                self._insert_coverage_seed(rid, cov_id, {
                    **cov,
                    "coverage_scope": _s(cov.get("coverage_type", "coverage")),
                    "decision": _s(cov.get("decision", cov.get("status", ""))),
                    "coverage_id": _s(cov.get("coverage_id", cov_id)),
                    "max_approved": _first_money(
                        cov,
                        "remaining_reimbursement_usd",
                        "reimbursement_cap_usd",
                        "limit_usd",
                        "approved_cap_usd",
                    ),
                    "summary": json.dumps(cov, default=str),
                })

    def _seed_options(self, seed_dir: Path) -> None:
        with open(seed_dir / "seed_options.json") as f:
            opts = json.load(f)

        for pid, plan in opts.get("service_options", {}).items():
            if isinstance(plan, dict):
                self._upsert_plan(pid, _s(plan.get("request_id", "")), plan)

        for mid, msg in opts.get("provider_messages", {}).items():
            if isinstance(msg, dict):
                self._insert_provider_thread_seed(mid, msg)

    # -- Phase 3: records.json ----------------------------------------------

    def _seed_records(self, seed_dir: Path) -> None:
        with open(seed_dir / "records.json") as f:
            recs = json.load(f)

        for key, val in recs.items():
            if not isinstance(val, dict):
                continue
            keys = set(val.keys())

            # --- Nested collection dicts ---
            if key == "requests":
                for rid, req in val.items():
                    if isinstance(req, dict):
                        self._seed_record_request(rid, req)
            elif key == "service_options":
                for pid, plan in val.items():
                    if isinstance(plan, dict):
                        self._upsert_plan(pid, _s(plan.get("request_id", "")), plan)
            elif key == "providers":
                for pid, prov in val.items():
                    if isinstance(prov, dict):
                        self._upsert_provider(pid, prov)
            elif key == "coverage_policies":
                for pid, pol in val.items():
                    if isinstance(pol, dict):
                        rid = _s(pol.get("request_id", ""))
                        self._insert_coverage_seed(rid, pid, {
                            "coverage_scope": "policy",
                            "decision": _s(pol.get("decision", pol.get("eligibility_status", ""))),
                            "summary": json.dumps(pol, default=str),
                            "policy_id": pid,
                            "max_approved": _f(pol.get("approved_cap_usd",
                                                       pol.get("budget_cap_usd", 0))),
                        })
            elif key == "provider_messages":
                for mid, msg in val.items():
                    if isinstance(msg, dict):
                        self._insert_provider_thread_seed(mid, msg)
            elif key == "submissions":
                for sid, sub in val.items():
                    if isinstance(sub, dict):
                        self.conn.execute(
                            "INSERT INTO service_submissions "
                            "(created_at, timestamp, request_id, submission_id, submission_type, "
                            "status, note, authored_by) VALUES (?,?,?,?,?,?,?,?)",
                            (_SEED_TS, _SEED_TS, _s(sub.get("request_id")), sid,
                             _s(sub.get("submission_type", "seed")),
                             _s(sub.get("status", "seed")),
                             json.dumps(sub, default=str), "seed"),
                        )
            elif key == "adjacent_entities":
                # Decoy / adjacent entity markers — load as contexts
                for eid, edata in val.items():
                    if isinstance(edata, dict):
                        self._upsert_context(eid, "", "adjacent_entity",
                                             json.dumps(edata, default=str))

            # --- Individual entries ---
            elif "customer_id" in keys and ("status" in keys or "category" in keys
                                            or "issue_type" in keys or "title" in keys
                                            or "objective" in keys or "summary" in keys):
                self._seed_record_request(key, val)

            elif "plan_name" in keys or ("service_type" in keys and "price" not in keys
                                         and "price_total_usd" in keys):
                self._upsert_plan(key, _s(val.get("request_id", "")), val)

            elif "service_type" in keys or "price" in keys or "price_total_usd" in keys or "price_estimate" in keys:
                self._upsert_plan(key, _s(val.get("request_id", "")), val)

            elif "file_id" in keys or "file_type" in keys or "attachment_id" in keys:
                self._seed_attachment_or_context(key, val)

            elif "coverage_id" in keys or "coverage_type" in keys or "approved_cap_usd" in keys:
                rid = _s(val.get("request_id", ""))
                self._insert_coverage_seed(rid, key, {
                    "coverage_scope": _s(val.get("coverage_type", "coverage")),
                    "decision": _s(val.get("decision", val.get("eligibility_status", ""))),
                    "summary": json.dumps(val, default=str),
                    "coverage_id": _s(val.get("coverage_id", key)),
                    "max_approved": _f(val.get("approved_cap_usd", 0)),
                })

            elif "thread_id" in keys or key.startswith("provider_message_thread") or key.startswith("provider_msg_thread"):
                self._insert_provider_thread_seed(key, val)

            elif "submission_id" in keys or "submission_type" in keys or key.startswith("reservation_submission") or key.startswith("submission_template"):
                rid = _s(val.get("request_id", ""))
                self.conn.execute(
                    "INSERT INTO service_submissions "
                    "(created_at, timestamp, request_id, submission_id, submission_type, "
                    "selected_option_id, provider_id, status, note, authored_by) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (_SEED_TS, _SEED_TS, rid, _s(val.get("submission_id", key)),
                     _s(val.get("submission_type", "seed")),
                     _s(val.get("selected_option_id", val.get("option_id", ""))),
                     _s(val.get("provider_id")),
                     _s(val.get("status", "seed")),
                     json.dumps(val, default=str), "seed"),
                )

            elif "provider_name" in keys or (key.startswith("wellness-") and "provider_id" not in keys):
                self._upsert_provider(key, val)

            elif val.get("record_type", "").startswith("provider_message"):
                self._insert_provider_thread_seed(key, {**val, "message_type": "template"})

            elif val.get("record_type", "").startswith("payment_authorization"):
                rid = _s(val.get("request_id", ""))
                self._insert_coverage_seed(rid, key, {
                    "coverage_scope": "payment_authorization",
                    "decision": _s(val.get("authorization_status", "approved")),
                    "summary": json.dumps(val, default=str),
                    "coverage_id": key,
                })

            elif "review_flags" in keys or "rank_hint" in keys:
                # Service plan review metadata — store as plan details
                pid = _s(val.get("plan_id", key))
                rid = _s(val.get("request_id", ""))
                existing = self.conn.execute(
                    "SELECT plan_id FROM service_plans WHERE plan_id = ?", (pid,)
                ).fetchone()
                if existing:
                    self.conn.execute(
                        "UPDATE service_plans SET details_json = ? WHERE plan_id = ?",
                        (json.dumps(val, default=str), pid),
                    )

            elif "decision_id" in keys or "authorization_rule" in keys:
                rid = _s(val.get("request_id", ""))
                self._insert_coverage_seed(rid, key, {
                    "coverage_scope": _s(val.get("record_type", "decision")),
                    "decision": _s(val.get("decision", val.get("current_ready_state", ""))),
                    "summary": json.dumps(val, default=str),
                    "coverage_id": _s(val.get("decision_id", val.get("authorization_id", key))),
                })

            elif "profile_snapshot" in keys or "customer_id" in keys:
                cid = _s(val.get("customer_id", ""))
                for scope_key, scope_val in val.items():
                    if scope_key == "customer_id":
                        continue
                    content = json.dumps(scope_val, default=str) if isinstance(scope_val, dict) else _s(scope_val)
                    self._upsert_context(cid, cid, scope_key, content)

            elif "suggested_message" in keys or "template_id" in keys:
                self._insert_provider_thread_seed(key, {**val, "message_type": "template"})

    # -----------------------------------------------------------------------
    # Seed helpers
    # -----------------------------------------------------------------------

    def _upsert_customer(self, cid: str, p: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO customers "
            "(customer_id, full_name, preferred_city, default_payment_ref, notes) "
            "VALUES (?, ?, ?, ?, ?)",
            (cid, _s(p.get("full_name")), _s(p.get("preferred_city")),
             _s(p.get("default_payment_ref")),
             _s(p.get("notes"))),
        )

    def _upsert_request(self, rid: str, p: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO requests "
            "(request_id, customer_id, family, source_benchmark, objective, "
            "budget_cap, preferred_window, status, payment_status, "
            "selected_plan_id, category, details_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, _s(p.get("customer_id")),
             _s(p.get("family", p.get("category", ""))),
             _s(p.get("source_benchmark")),
             _s(p.get("objective", p.get("title", p.get("summary", "")))),
             _request_budget_cap(p),
             _s(p.get("preferred_window")),
             _s(p.get("status", "open")),
             _s(p.get("payment_status", "pending")),
             _s(p.get("selected_plan_id")),
             _s(p.get("category")),
             json.dumps({k: v for k, v in p.items()
                         if k not in ("customer_id", "family", "source_benchmark",
                                      "objective", "budget_cap", "preferred_window",
                                      "status", "payment_status", "selected_plan_id",
                                      "contexts", "plans", "coverage_checks", "category")},
                        default=str)),
        )

    def _upsert_context(self, rid: str, cid: str, scope: str, content: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO customer_contexts "
            "(request_id, customer_id, context_scope, content) VALUES (?, ?, ?, ?)",
            (rid, cid, scope, content),
        )

    def _upsert_plan(self, pid: str, rid: str, plan: dict) -> None:
        rid = _s(rid or plan.get("request_id", ""))
        price = self._price_from_plan(plan)
        self.conn.execute(
            "INSERT OR REPLACE INTO service_plans "
            "(plan_id, request_id, service_type, provider_id, title, price, "
            "schedule, eligibility, details, status, details_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pid, rid,
             self._infer_service_type(pid, plan),
             _s(plan.get("provider_id", plan.get("provider",
                          plan.get("provider_name", "")))),
             _first_text(plan, "title", "plan_name", "display_name", "name",
                         "option_name", "bundle_name", "membership_name",
                         "provider_name") or pid,
             price,
             _first_text(plan, "schedule", "availability_window",
                         "availability_snapshot", "availability",
                         "schedule_summary", "requested_datetime_local") or
             self._term_window(plan),
             _first_text(plan, "eligibility", "fit_summary",
                         "fit_notes", "dietary_fit", "clinical_fit_summary",
                         "coverage_summary", "availability_status",
                         "hold_status", "status"),
             _first_text(plan, "details", "description", "summary", "includes",
                         "pricing_summary", "cancellation_policy_summary",
                         "search_summary", "location_summary", "features",
                         "notes", "review_note", "message_guidance"),
             _s(plan.get("status", "active")),
             json.dumps(plan, default=str)),
        )

    def _price_from_plan(self, plan: dict) -> float:
        pricing = plan.get("pricing", {})
        if isinstance(pricing, dict):
            nested_price = _first_money(
                pricing, "lodging_total", "total", "grand_total",
                "estimated_total_usd", "base_price_usd",
            )
            if nested_price:
                return nested_price
        details = plan.get("details", {})
        if isinstance(details, dict):
            nested_price = _first_money(
                details, "estimated_total_usd", "price_total_usd",
                "monthly_rate_usd", "base_price_usd", "deposit_usd",
            )
            if nested_price:
                return nested_price
        price = _first_money(
            plan,
            "price", "price_total_usd", "price_estimate", "price_usd",
            "estimated_total_usd", "total_estimated_cost_usd",
            "monthly_rate_usd", "first_visit_cost_usd", "base_price_usd",
            "deposit_due_usd", "authorized_amount_usd",
        )
        if price:
            return price
        fees = plan.get("fees", {})
        if isinstance(fees, dict):
            return sum(_parse_money(value) for value in fees.values())
        return 0.0

    def _term_window(self, plan: dict) -> str:
        start = _s(plan.get("term_start", ""))
        end = _s(plan.get("term_end", ""))
        if start or end:
            return f"{start} to {end}".strip()
        return ""

    def _infer_service_type(self, pid: str, plan: dict) -> str:
        explicit = _first_text(plan, "service_type", "plan_family",
                               "service_category", "option_type")
        text_parts = [pid, explicit]
        for key in (
            "category", "title", "plan_name", "display_name", "name", "option_name",
            "bundle_name", "membership_name", "provider_id", "provider_name",
            "summary", "details", "cancellation_policy_summary",
            "search_summary", "location_summary", "features", "notes",
        ):
            text_parts.append(_json_text(plan.get(key)))
        text = _norm(" ".join(text_parts))
        if "rental bundle" in text or "rentalbundle" in text or "furnished" in text or "listing" in text:
            return "rental_bundle"
        if "move" in text or "mover" in text or "moving" in text or "storage" in text or "relocation" in text:
            return "moving_storage_bundle"
        if "private dining" in text or "dining" in text or "restaurant" in text or "meal" in text:
            return "meal_delivery" if "meal" in text and "private dining" not in text else "private_dining"
        if "physical therapy" in text or "counseling" in text or "wellness" in text or "rehab" in text:
            return "wellness_program"
        if "airport" in text or "hotel" in text or "rail" in text or "flight" in text or "travel" in text:
            return "travel_bundle"
        category = _s(plan.get("category", "")).strip()
        return explicit or category or ""

    def _insert_coverage_seed(self, rid: str, pid: str, cov: dict) -> None:
        self.conn.execute(
            "INSERT INTO coverage_checks "
            "(created_at, request_id, plan_id, option_id, bundle_id, "
            "option_ref, coverage_id, budget_record_id, policy_id, "
            "coverage_scope, decision, estimated_out_of_pocket, max_approved, "
            "summary, authored_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (_SEED_TS, rid, pid,
             _s(cov.get("option_id", pid)),
             _s(cov.get("bundle_id", pid)),
             _s(cov.get("option_ref", pid)),
             _s(cov.get("coverage_id")),
             _s(cov.get("budget_record_id")),
             _s(cov.get("policy_id")),
             _s(cov.get("coverage_scope")),
             _s(cov.get("decision", cov.get("status", ""))),
             _parse_money(cov.get("estimated_out_of_pocket", 0)),
             _parse_money(cov.get("max_approved", cov.get("approved_cap_usd", 0))),
             _s(cov.get("summary")) or json.dumps(cov, default=str),
             "seed"),
        )

    def _insert_provider_thread_seed(self, thread_id: str, msg: dict) -> None:
        self.conn.execute(
            "INSERT INTO provider_threads "
            "(timestamp, request_id, provider_id, actor_role, role, "
            "message_type, purpose, bundle_id, option_id, template_id, "
            "message_id, content, authored_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                _SEED_TS,
                _s(msg.get("request_id")),
                _s(msg.get("provider_id", msg.get("target_provider", ""))),
                "seed",
                "seed",
                _s(msg.get("message_type", msg.get("channel", "seed"))),
                _first_text(msg, "purpose", "suggested_scope", "allowed_scope",
                            "approved_scope", "topic"),
                _s(msg.get("bundle_id", msg.get("intended_provider_bundle_id", ""))),
                _s(msg.get("option_id", msg.get("selected_option_id", ""))),
                _s(msg.get("template_id", "")),
                _s(msg.get("thread_id", msg.get("message_id", thread_id))),
                _first_text(msg, "content", "message_body", "draft_message",
                            "draft_text", "message_text", "message_brief",
                            "latest_context") or json.dumps(msg, default=str),
                "seed",
            ),
        )

    def _materialize_contracts_as_plans(self) -> None:
        rows = list(self.conn.execute("SELECT * FROM contracts").fetchall())
        for row in rows:
            contract = dict(row)
            rid = _s(contract.get("request_id", ""))
            if not rid:
                rid = self._infer_request_id_for_record(contract["contract_id"], contract)
            if not rid:
                continue
            if not _s(contract.get("request_id", "")):
                self.conn.execute(
                    "UPDATE contracts SET request_id = ? WHERE contract_id = ?",
                    (rid, contract["contract_id"]),
                )
            plan = {
                **contract,
                "request_id": rid,
                "status": contract.get("status", "active"),
                "title": contract.get("membership_name") or contract.get("provider_name"),
                "price": contract.get("monthly_rate_usd", 0),
                "details": " | ".join(part for part in [
                    _s(contract.get("provider_name")),
                    _s(contract.get("cancellation_policy_summary")),
                    _s(contract.get("autopay_ref")),
                    _s(contract.get("member_account_ref")),
                ] if part),
            }
            self._upsert_plan(contract["contract_id"], rid, plan)

    def _infer_request_id_for_record(self, key: str, record: dict) -> str:
        explicit = _s(record.get("request_id", "")).strip()
        if explicit:
            return explicit

        customer_id = _s(record.get("customer_id", "")).strip()
        if customer_id:
            suffix_match = re.match(r"^DL-CUST-(\d+)$", customer_id)
            if suffix_match:
                candidate = f"DL-{suffix_match.group(1)}"
                if self._request_row(candidate) is not None:
                    return candidate

            req_rows = list(self.conn.execute(
                "SELECT request_id, status FROM requests WHERE customer_id = ?",
                (customer_id,),
            ).fetchall())
            current_rows = [row for row in req_rows if _status_is_current(row["status"])]
            if len(current_rows) == 1:
                return _s(current_rows[0]["request_id"])
            if len(req_rows) == 1:
                return _s(req_rows[0]["request_id"])

        match = re.search(r"DL[-_]?0*([0-9]{1,3})", key, re.IGNORECASE)
        if match:
            candidate = f"DL-{int(match.group(1)):03d}"
            if self._request_row(candidate) is not None:
                return candidate
        return ""

    def _upsert_provider(self, pid: str, p: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO providers "
            "(provider_id, provider_name, related_option_id, review_notes, "
            "message_guidance, details_json) VALUES (?,?,?,?,?,?)",
            (pid, _s(p.get("provider_name", p.get("name", ""))),
             _s(p.get("related_option_id")),
             _s(p.get("review_notes")),
             _s(p.get("message_guidance")),
             json.dumps(p, default=str)),
        )

    def _seed_attachment_or_context(self, key: str, val: dict) -> None:
        rid = _s(val.get("request_id", ""))
        scope = f"attachment:{_s(val.get('file_type', val.get('type', 'document')))}"
        content = _s(val.get("extracted_text",
                              val.get("notes",
                                      val.get("content", json.dumps(val, default=str)))))
        self._upsert_context(rid, "", scope, f"[{key}] {content}")

    def _seed_record_request(self, rid: str, req: dict) -> None:
        """Seed a request entry from records.json — may also create customer."""
        rid = _s(req.get("request_id", rid)) or rid
        cid = _s(req.get("customer_id", ""))
        # Ensure customer exists (minimal stub)
        if cid:
            existing = self.conn.execute(
                "SELECT customer_id FROM customers WHERE customer_id = ?", (cid,)
            ).fetchone()
            if not existing:
                self._upsert_customer(cid, {
                    "full_name": cid,
                    "preferred_city": "",
                    "default_payment_ref": _s(req.get("payment_path",
                                                       req.get("default_payment_ref", ""))),
                    "notes": "",
                })
        self._upsert_request(rid, req)

        # Inline contexts from nested fields
        for ctx_key in ("constraints", "timing", "timing_constraints",
                        "timing_context", "address_context", "budget_scope",
                        "budget_context", "payment_path", "payment_scope",
                        "contact_context", "operational_notes", "attachments",
                        "medical_context", "medical_notes_summary",
                        "coverage_context", "pet_summary",
                        "dietary_constraints", "recovery_reason",
                        "internal_notes", "home_context"):
            val = req.get(ctx_key)
            if val is not None:
                content = json.dumps(val, default=str) if isinstance(val, (dict, list)) else _s(val)
                self._upsert_context(rid, cid, ctx_key, content)

        # Inline attachments
        for att in req.get("attachments", []):
            if isinstance(att, dict):
                att_id = _s(att.get("attachment_id", att.get("file_id", "")))
                self._seed_attachment_or_context(att_id or f"{rid}-att", att)

        # Inline plans
        for pid, plan in req.get("plans", {}).items():
            if isinstance(plan, dict):
                self._upsert_plan(pid, rid, plan)

    # -----------------------------------------------------------------------
    # Public interface
    # -----------------------------------------------------------------------

    def set_current_agent(self, role: str | None) -> None:
        self._current_agent_role = role

    def apply_task_metadata(self, metadata: dict[str, Any]) -> None:
        request_id = str(metadata.get("request_id", "")).strip()
        customer_id = str(metadata.get("customer_id", "")).strip()
        self._active_request_id = request_id or None
        self._active_customer_id = customer_id or None

    def dispatch(self, tool_name: str, args: dict[str, Any]) -> str:
        role_val = self._current_agent_role or ""
        try:
            self.conn.execute(
                "INSERT INTO audit_log "
                "(timestamp, tool, tool_name, args_json, "
                "agent_role, actor_role, role, "
                "customer_id, request_id, resource_id, attachment_id, "
                "target_id, bundle_id, option_id, provider_record_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_now_iso(), tool_name, tool_name,
                 json.dumps(args, default=str),
                 role_val, role_val, role_val,
                 _s(args.get("customer_id")),
                 _s(args.get("request_id")),
                 _s(args.get("resource_id", args.get("file_id", ""))),
                 _s(args.get("attachment_id")),
                 _s(args.get("target_id", args.get("thread_id",
                              args.get("message_id", args.get("evidence_id",
                              args.get("option_id", "")))))),
                 _s(args.get("bundle_id")),
                 _s(args.get("option_id", args.get("plan_id", ""))),
                 _s(args.get("provider_record_id",
                              args.get("provider_id", "")))),
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

    def reconcile_trace_actions(self, actions: list[Any]) -> None:
        """Backfill caller roles from normalized traces for MCP-backed runs."""

        def derive_fields(action: Any) -> dict[str, Any]:
            args = action_args(action)
            tool_name = str(getattr(action, "tool_name", "") or "")
            role = action_role(action)
            return {
                "tool": tool_name,
                "tool_name": tool_name,
                "args_json": json_args(action),
                "agent_role": role,
                "actor_role": role,
                "role": role,
                "customer_id": _s(args.get("customer_id")),
                "request_id": _s(args.get("request_id")),
                "resource_id": _s(args.get("resource_id", args.get("file_id", ""))),
                "attachment_id": _s(args.get("attachment_id")),
                "target_id": _s(args.get(
                    "target_id",
                    args.get(
                        "thread_id",
                        args.get("message_id", args.get("evidence_id", args.get("option_id", ""))),
                    ),
                )),
                "bundle_id": _s(args.get("bundle_id")),
                "option_id": _s(args.get("option_id", args.get("plan_id", ""))),
                "provider_record_id": _s(args.get("provider_record_id", args.get("provider_id", ""))),
            }

        reconcile_audit_log(self.conn, actions, derive_fields)
        backfill_write_roles(
            self.conn,
            actions,
            [
                ToolWriteBackfill("coverage_checks", "authored_by", "check_budget_coverage"),
                ToolWriteBackfill("provider_threads", "authored_by", "message_provider"),
                ToolWriteBackfill("payment_events", "authored_by", "authorize_payment"),
                ToolWriteBackfill("service_submissions", "authored_by", "submit_service_request"),
            ],
        )
        self.conn.commit()

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
    # Internal resolution helpers
    # -----------------------------------------------------------------------

    def _resolve_request_id(self, request_id: str = "") -> str:
        return str(request_id or self._active_request_id or "").strip()

    def _normalize_customer_id(self, customer_id: str) -> str:
        customer_id = str(customer_id or "").strip()
        if not customer_id:
            return customer_id
        row = self.conn.execute(
            "SELECT customer_id FROM customers WHERE customer_id = ?",
            (customer_id,),
        ).fetchone()
        if row:
            return row["customer_id"]
        if customer_id.startswith("DL-") and "CUST" not in customer_id:
            expanded = customer_id.replace("DL-", "DL-CUST-", 1)
            row = self.conn.execute(
                "SELECT customer_id FROM customers WHERE customer_id = ?",
                (expanded,),
            ).fetchone()
            if row:
                return row["customer_id"]
        row = self.conn.execute(
            "SELECT customer_id FROM customers WHERE full_name LIKE ?",
            (f"%{customer_id}%",),
        ).fetchone()
        if row:
            return row["customer_id"]
        return customer_id

    def _resolve_customer_id(self, request_id: str, customer_id: str = "") -> str:
        explicit = str(customer_id or self._active_customer_id or "").strip()
        if explicit:
            return self._normalize_customer_id(explicit)
        row = self._request_row(request_id)
        return str(row["customer_id"]).strip() if row is not None else ""

    def _request_row(self, request_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM requests WHERE request_id = ?", (request_id,),
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
        return filter_rows_by_query(rows, query, fields=fields)

    def _plan_row_for_option(self, request_id: str, option_id: str) -> sqlite3.Row | None:
        option_id = _s(option_id).strip()
        if not option_id:
            return None
        return self.conn.execute(
            "SELECT * FROM service_plans WHERE request_id = ? AND plan_id = ?",
            (request_id, option_id),
        ).fetchone()

    def _first_current_plan_row(self, request_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM service_plans WHERE request_id = ? "
            "ORDER BY CASE "
            "WHEN lower(status) LIKE '%active%' THEN 0 "
            "WHEN lower(status) LIKE '%available%' THEN 0 "
            "WHEN lower(status) LIKE '%current%' THEN 0 "
            "WHEN lower(status) LIKE '%holdable%' THEN 0 "
            "ELSE 1 END, "
            "CASE WHEN provider_id = '' THEN 1 ELSE 0 END, "
            "CASE WHEN price <= 0 THEN 1 ELSE 0 END, "
            "price ASC, plan_id LIMIT 1",
            (request_id,),
        ).fetchone()

    def _resolve_plan_for_mutation(
        self,
        request_id: str,
        *candidate_ids: str,
    ) -> tuple[str, sqlite3.Row | None, str]:
        explicit = next((_s(value).strip() for value in candidate_ids
                         if _s(value).strip()), "")
        if explicit:
            row = self._plan_row_for_option(request_id, explicit)
            if row is None:
                return explicit, None, (
                    f"Service option '{explicit}' is not registered for request "
                    f"{request_id}; refusing to write payment/submission state."
                )
            return explicit, row, ""

        row = self._first_current_plan_row(request_id)
        if row is None:
            return "", None, f"No service plans available for request {request_id}."
        return _s(row["plan_id"]), row, ""

    def _coverage_row_for(
        self,
        request_id: str,
        *,
        coverage_id: str = "",
        budget_record_id: str = "",
        policy_id: str = "",
        option_id: str = "",
    ) -> sqlite3.Row | None:
        if coverage_id:
            return self.conn.execute(
                "SELECT * FROM coverage_checks WHERE request_id = ? "
                "AND coverage_id = ? ORDER BY seq LIMIT 1",
                (request_id, coverage_id),
            ).fetchone()
        if budget_record_id:
            return self.conn.execute(
                "SELECT * FROM coverage_checks WHERE request_id = ? "
                "AND budget_record_id = ? ORDER BY seq LIMIT 1",
                (request_id, budget_record_id),
            ).fetchone()
        if policy_id:
            return self.conn.execute(
                "SELECT * FROM coverage_checks WHERE request_id = ? "
                "AND policy_id = ? ORDER BY seq LIMIT 1",
                (request_id, policy_id),
            ).fetchone()
        if option_id:
            return self.conn.execute(
                "SELECT * FROM coverage_checks WHERE request_id = ? "
                "AND (plan_id = ? OR option_id = ? OR bundle_id = ? OR option_ref = ?) "
                "ORDER BY seq LIMIT 1",
                (request_id, option_id, option_id, option_id, option_id),
            ).fetchone()
        return None

    def _thread_row_for(self, request_id: str, thread_id: str) -> sqlite3.Row | None:
        thread_id = _s(thread_id).strip()
        if not thread_id:
            return None
        return self.conn.execute(
            "SELECT * FROM provider_threads WHERE request_id = ? "
            "AND (message_id = ? OR template_id = ?) ORDER BY seq LIMIT 1",
            (request_id, thread_id, thread_id),
        ).fetchone()

    def _provider_bound_to_request(self, request_id: str, provider_id: str) -> bool:
        provider_id = _s(provider_id).strip()
        if not provider_id:
            return False
        plan_row = self.conn.execute(
            "SELECT 1 FROM service_plans WHERE request_id = ? "
            "AND (provider_id = ? OR plan_id = ?) LIMIT 1",
            (request_id, provider_id, provider_id),
        ).fetchone()
        if plan_row is not None:
            return True
        thread_row = self.conn.execute(
            "SELECT 1 FROM provider_threads WHERE request_id = ? "
            "AND provider_id = ? LIMIT 1",
            (request_id, provider_id),
        ).fetchone()
        return thread_row is not None

    # -----------------------------------------------------------------------
    # Tool handlers — READ
    # -----------------------------------------------------------------------

    def tool_get_customer_context(
        self,
        customer_id: str = "",
        context_scope: str = "",
        **kwargs: Any,
    ) -> str:
        request_id = self._resolve_request_id(kwargs.get("request_id", ""))
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
        # Also include contexts keyed by customer_id
        cid_rows = list(self.conn.execute(
            "SELECT * FROM customer_contexts WHERE request_id = ? ORDER BY context_scope",
            (resolved_customer_id,),
        ).fetchall())
        all_ctx = {(r["request_id"], r["context_scope"]): r for r in rows}
        for r in cid_rows:
            all_ctx.setdefault((r["request_id"], r["context_scope"]), r)
        all_rows = list(all_ctx.values())

        selected = self._filter_rows(all_rows, context_scope, fields=("context_scope",))
        if not selected:
            available = ", ".join(sorted(set(r["context_scope"] for r in all_rows))) if all_rows else "(none)"
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

        rows = self._rows_for_request(
            "service_plans",
            resolved_request_id,
            "CASE "
            "WHEN lower(status) LIKE '%active%' THEN 0 "
            "WHEN lower(status) LIKE '%available%' THEN 0 "
            "WHEN lower(status) LIKE '%current%' THEN 0 "
            "WHEN lower(status) LIKE '%holdable%' THEN 0 "
            "ELSE 1 END, price ASC, plan_id",
        )
        query = ",".join(
            part for part in [
                service_type,
                kwargs.get("service_family", ""),
                constraint_bundle,
            ]
            if str(part).strip()
        )
        selected = self._filter_rows(
            rows, query,
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
        file_id: str = "",
        evidence_id: str = "",
        bundle_id: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_request_id = self._resolve_request_id(request_id)
        if not resolved_request_id:
            return "Service option request is missing request_id."

        # If a file_id or evidence_id is provided, try attachment lookup first
        resolved_file_id = _s(file_id or evidence_id).strip()
        if resolved_file_id:
            att_rows = list(self.conn.execute(
                "SELECT * FROM customer_contexts "
                "WHERE (request_id = ? OR request_id = '') "
                "AND context_scope LIKE 'attachment:%' "
                "AND content LIKE ?",
                (resolved_request_id, f"%[{resolved_file_id}]%"),
            ).fetchall())
            if not att_rows:
                att_rows = list(self.conn.execute(
                    "SELECT * FROM customer_contexts "
                    "WHERE context_scope LIKE 'attachment:%' "
                    "AND content LIKE ?",
                    (f"%{resolved_file_id}%",),
                ).fetchall())
            if att_rows:
                lines = [f"Attachment Review - {resolved_file_id} ({resolved_request_id})"]
                for att_row in att_rows:
                    lines.append(f"  Scope: {att_row['context_scope']}")
                    lines.append(f"  Content: {att_row['content']}")
                return "\n".join(lines)

        # Resolve option_id from bundle_id if option_id is empty
        if not str(option_id).strip():
            option_id = _s(bundle_id).strip()
        if not str(option_id).strip():
            return "Service option request is missing option_id."

        row = self.conn.execute(
            "SELECT * FROM service_plans WHERE request_id = ? AND plan_id = ?",
            (resolved_request_id, option_id),
        ).fetchone()

        # Fallback: search by plan_id alone (cross-request lookup)
        if row is None:
            row = self.conn.execute(
                "SELECT * FROM service_plans WHERE plan_id = ?", (option_id,),
            ).fetchone()

        # Try contracts table
        if row is None:
            contract = self.conn.execute(
                "SELECT * FROM contracts WHERE contract_id = ? "
                "AND (request_id = ? OR request_id = '')",
                (option_id, resolved_request_id),
            ).fetchone()
            if contract:
                return "\n".join([
                    f"Contract Detail - {option_id}",
                    f"Request: {contract['request_id']}",
                    f"Customer: {contract['customer_id']}",
                    f"Provider: {contract['provider_name']} ({contract['provider_id']})",
                    f"Membership: {contract['membership_name']}",
                    f"Status: {contract['status']}",
                    f"Term: {contract['term_start']} to {contract['term_end']}",
                    f"Rate: {_money(float(contract['monthly_rate_usd']))}/month",
                    f"Autopay: {contract['autopay_ref']}",
                    f"Cancellation: {contract['cancellation_policy_summary']}",
                    f"Account Ref: {contract['member_account_ref']}",
                ])

        # Try providers table
        if row is None:
            provider = self.conn.execute(
                "SELECT * FROM providers WHERE provider_id = ?", (option_id,),
            ).fetchone()
            if provider:
                return "\n".join([
                    f"Provider Detail - {option_id}",
                    f"Name: {provider['provider_name']}",
                    f"Related Option: {provider['related_option_id']}",
                    f"Review Notes: {provider['review_notes']}",
                    f"Message Guidance: {provider['message_guidance']}",
                ])

        # Try customer_contexts for attachments using option_id as fallback key
        if row is None:
            fallback_fid = _s(file_id or evidence_id or option_id).strip()
            att_rows = list(self.conn.execute(
                "SELECT * FROM customer_contexts "
                "WHERE (request_id = ? OR request_id = '') "
                "AND context_scope LIKE 'attachment:%' "
                "AND content LIKE ?",
                (resolved_request_id, f"%[{fallback_fid}]%"),
            ).fetchall())
            if not att_rows:
                att_rows = list(self.conn.execute(
                    "SELECT * FROM customer_contexts "
                    "WHERE context_scope LIKE 'attachment:%' "
                    "AND content LIKE ?",
                    (f"%{fallback_fid}%",),
                ).fetchall())
            if att_rows:
                lines = [f"Attachment Review - {fallback_fid} ({resolved_request_id})"]
                for att_row in att_rows:
                    lines.append(f"  Scope: {att_row['context_scope']}")
                    lines.append(f"  Content: {att_row['content']}")
                return "\n".join(lines)

        if row is None:
            return f"Service plan not found: {option_id}"

        coverage_row = self.conn.execute(
            "SELECT * FROM coverage_checks WHERE request_id = ? "
            "AND (plan_id = ? OR option_id = ? OR bundle_id = ?) "
            "ORDER BY seq LIMIT 1",
            (resolved_request_id, option_id, option_id, option_id),
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

        all_rows = self._rows_for_request(
            "service_plans",
            resolved_request_id,
            "CASE "
            "WHEN lower(status) LIKE '%active%' THEN 0 "
            "WHEN lower(status) LIKE '%available%' THEN 0 "
            "WHEN lower(status) LIKE '%current%' THEN 0 "
            "WHEN lower(status) LIKE '%holdable%' THEN 0 "
            "ELSE 1 END, price ASC, plan_id",
        )
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

    # -----------------------------------------------------------------------
    # Tool handlers — MUTATE
    # -----------------------------------------------------------------------

    def tool_check_budget_coverage(
        self,
        request_id: str = "",
        plan_id: str = "",
        option_id: str = "",
        bundle_id: str = "",
        coverage_id: str = "",
        budget_record_id: str = "",
        policy_id: str = "",
        option_ref: str = "",
        max_approved: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_request_id = self._resolve_request_id(request_id)
        if not resolved_request_id:
            return "Budget coverage request is missing request_id."

        resolved_option = _s(option_id or bundle_id or option_ref or plan_id).strip()
        plan_row = None
        if resolved_option:
            plan_row = self._plan_row_for_option(resolved_request_id, resolved_option)
            if plan_row is None:
                return (
                    f"Service option '{resolved_option}' is not registered for request "
                    f"{resolved_request_id}; refusing to run budget coverage."
                )
        elif not (coverage_id or budget_record_id or policy_id):
            plan_row = self._first_current_plan_row(resolved_request_id)
            resolved_option = str(plan_row["plan_id"]).strip() if plan_row else ""
            if not resolved_option:
                return f"No service plans available for request {resolved_request_id}."

        # Look up existing seed coverage by explicit coverage handle first, then option.
        row = self._coverage_row_for(
            resolved_request_id,
            coverage_id=_s(coverage_id),
            budget_record_id=_s(budget_record_id),
            policy_id=_s(policy_id),
            option_id=resolved_option,
        )

        request_row = self._request_row(resolved_request_id)
        budget_cap = float(request_row["budget_cap"]) if request_row is not None else 0.0

        if row is None:
            policy_plan_row = (
                self._plan_row_for_option(resolved_request_id, _s(policy_id))
                if policy_id else None
            )
            if policy_plan_row is not None:
                plan_price = float(plan_row["price"]) if plan_row is not None else float(policy_plan_row["price"])
                oop = max(0.0, plan_price - budget_cap) if budget_cap else plan_price
                decision = "within_budget" if budget_cap and plan_price <= budget_cap else "policy_reviewed"
                summary_text = (
                    f"Policy/authorization {policy_id} is registered for {resolved_request_id}; "
                    f"checked selected option {resolved_option or policy_id}."
                )
            elif coverage_id or budget_record_id or policy_id:
                handle = _s(coverage_id or budget_record_id or policy_id)
                return f"Coverage record not found for {resolved_request_id}: {handle}"
            elif plan_row is None:
                return (
                    f"Service option '{resolved_option}' is not registered for request "
                    f"{resolved_request_id}; refusing to run budget coverage."
                )
            else:
                # No seed coverage — create one from plan data
                plan_price = float(plan_row["price"])
                oop = max(0.0, plan_price - budget_cap) if budget_cap else plan_price
                decision = "within_budget" if plan_price <= budget_cap else "over_budget"
                summary_text = f"Auto-computed: plan {resolved_option} at {_money(plan_price)} vs cap {_money(budget_cap)}"
        else:
            if not resolved_option:
                resolved_option = _s(row["plan_id"] or row["option_id"] or
                                     row["bundle_id"] or row["option_ref"])
            plan_price = float(plan_row["price"]) if plan_row is not None else 0.0
            decision = row["decision"] or (
                "within_budget" if plan_row is not None and plan_price <= budget_cap else "reviewed"
            )
            oop = float(row["estimated_out_of_pocket"])
            if plan_row is not None and not oop:
                oop = max(0.0, plan_price - budget_cap) if budget_cap else plan_price
            summary_text = row["summary"] or f"Coverage record {row['coverage_id']} reviewed."

        # Write a coverage_checks row for the agent's check
        now = _now_iso()
        self.conn.execute(
            "INSERT INTO coverage_checks "
            "(created_at, request_id, plan_id, option_id, bundle_id, "
            "option_ref, coverage_id, budget_record_id, policy_id, "
            "coverage_scope, decision, estimated_out_of_pocket, max_approved, "
            "summary, authored_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (now, resolved_request_id, _s(plan_id),
             _s(option_id or resolved_option),
             _s(bundle_id or resolved_option),
             _s(option_ref or resolved_option),
             _s(coverage_id), _s(budget_record_id), _s(policy_id),
             "agent_check", decision, oop,
             _f(max_approved) or budget_cap,
             summary_text, self._current_agent_role or ""),
        )
        self.conn.commit()

        return "\n".join([
            f"Budget/Coverage Check - {resolved_request_id}",
            f"Plan: {resolved_option}",
            f"Budget Cap: {_money(budget_cap)}",
            f"Decision: {decision}",
            f"Estimated Out-of-Pocket: {_money(oop)}",
            f"Summary: {summary_text}",
        ])

    def tool_submit_service_request(
        self,
        request_id: str = "",
        plan_id: str = "",
        submission_type: str = "",
        submission_note: str = "",
        option_id: str = "",
        bundle_id: str = "",
        selected_option_id: str = "",
        plan_ref: str = "",
        submission_id: str = "",
        template_id: str = "",
        provider_id: str = "",
        customer_id: str = "",
        authorized_amount: str = "",
        scheduled_time: str = "",
        linked_authorization: str = "",
        schedule: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_request_id = self._resolve_request_id(request_id)
        if not resolved_request_id:
            return "Service submission request is missing request_id."

        resolved_option, plan_row, plan_error = self._resolve_plan_for_mutation(
            resolved_request_id,
            option_id,
            bundle_id,
            selected_option_id,
            plan_ref,
            plan_id,
        )
        if plan_error:
            return plan_error

        resolved_customer = self._resolve_customer_id(resolved_request_id, customer_id)
        # Resolve provider_id from plan if not given
        resolved_provider = _s(provider_id).strip()
        if not resolved_provider:
            resolved_provider = str(plan_row["provider_id"]).strip() if plan_row else ""

        submission_kind = str(submission_type or "booking").strip()
        now = _now_iso()
        self.conn.execute(
            "INSERT INTO service_submissions "
            "(created_at, timestamp, request_id, customer_id, plan_id, "
            "option_id, bundle_id, selected_option_id, plan_ref, "
            "submission_id, template_id, provider_id, submission_type, "
            "note, status, authored_by, authorized_amount, "
            "scheduled_time, linked_authorization, schedule) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (now, now, resolved_request_id, resolved_customer,
             _s(plan_id or resolved_option),
             _s(option_id or resolved_option),
             _s(bundle_id or resolved_option),
             _s(selected_option_id or resolved_option),
             _s(plan_ref or resolved_option),
             _s(submission_id), _s(template_id), resolved_provider,
             submission_kind,
             str(submission_note or "").strip(),
             "submitted",
             self._current_agent_role or "unknown",
             _f(authorized_amount),
             _s(scheduled_time), _s(linked_authorization), _s(schedule)),
        )
        self.conn.execute(
            "UPDATE requests SET selected_plan_id = ?, status = ? WHERE request_id = ?",
            (resolved_option, f"submitted:{submission_kind}", resolved_request_id),
        )
        self.conn.commit()
        return f"Submitted {submission_kind} for {resolved_request_id} using plan {resolved_option}."

    def tool_authorize_payment(
        self,
        request_id: str = "",
        plan_id: str = "",
        amount_limit: str = "",
        payment_reference: str = "",
        option_id: str = "",
        bundle_id: str = "",
        selected_option: str = "",
        coverage_id: str = "",
        policy_id: str = "",
        payment_ref: str = "",
        payment_path: str = "",
        authorization_id: str = "",
        authorization_note_id: str = "",
        amount: str = "",
        amount_usd: str = "",
        currency: str = "USD",
        customer_id: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_request_id = self._resolve_request_id(request_id)
        if not resolved_request_id:
            return "Payment authorization request is missing request_id."

        request_row = self._request_row(resolved_request_id)
        if request_row is None:
            return f"Request not found: {resolved_request_id}"

        resolved_option, plan_row, plan_error = self._resolve_plan_for_mutation(
            resolved_request_id,
            option_id,
            bundle_id,
            selected_option,
            plan_id,
            request_row["selected_plan_id"],
        )
        if plan_error:
            return plan_error
        plan_price = float(plan_row["price"]) if plan_row is not None else 0.0

        resolved_customer = self._resolve_customer_id(resolved_request_id, customer_id)

        resolved_payment_ref = _s(payment_ref or payment_reference).strip()
        if not resolved_payment_ref:
            cust_row = self.conn.execute(
                "SELECT default_payment_ref FROM customers WHERE customer_id = ?",
                (resolved_customer,),
            ).fetchone()
            resolved_payment_ref = str(cust_row["default_payment_ref"]).strip() if cust_row else ""

        resolved_amount = (_parse_money(amount) or _parse_money(amount_usd)
                           or _parse_money(amount_limit) or plan_price)

        now = _now_iso()
        self.conn.execute(
            "INSERT INTO payment_events "
            "(created_at, timestamp, request_id, customer_id, "
            "plan_id, option_id, bundle_id, selected_option, "
            "coverage_id, policy_id, event_type, "
            "amount, amount_usd, amount_limit, currency, "
            "payment_ref, payment_path, payment_reference, "
            "authorization_id, authorization_note_id, "
            "status, note, authored_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (now, now, resolved_request_id, resolved_customer,
             _s(plan_id or resolved_option),
             _s(option_id or resolved_option),
             _s(bundle_id or resolved_option),
             _s(selected_option or resolved_option),
             _s(coverage_id), _s(policy_id),
             "authorize_payment",
             resolved_amount, resolved_amount, resolved_amount,
             _s(currency or "USD"),
             resolved_payment_ref, _s(payment_path), resolved_payment_ref,
             _s(authorization_id), _s(authorization_note_id),
             "authorized",
             f"Authorized up to {_money(resolved_amount)}",
             self._current_agent_role or "unknown"),
        )
        self.conn.execute(
            "UPDATE requests SET payment_status = ?, selected_plan_id = ? WHERE request_id = ?",
            ("authorized", resolved_option, resolved_request_id),
        )
        self.conn.commit()
        return (
            f"Authorized payment for {resolved_request_id} using {resolved_option} "
            f"up to {_money(resolved_amount)}."
        )

    def tool_message_provider(
        self,
        request_id: str = "",
        provider_id: str = "",
        message_type: str = "",
        content: str = "",
        purpose: str = "",
        bundle_id: str = "",
        option_id: str = "",
        template_id: str = "",
        message_id: str = "",
        thread_id: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_request_id = self._resolve_request_id(request_id)
        if not resolved_request_id:
            return "Provider message request is missing request_id."

        resolved_provider = str(provider_id or "").strip()
        resolved_thread_id = _s(thread_id or message_id).strip()
        thread_row = self._thread_row_for(resolved_request_id, resolved_thread_id)

        # Older task data sometimes placed the provider thread id in provider_id.
        if thread_row is None and resolved_provider:
            maybe_thread = self._thread_row_for(resolved_request_id, resolved_provider)
            if maybe_thread is not None:
                thread_row = maybe_thread
                resolved_thread_id = _s(thread_row["message_id"])
                resolved_provider = _s(thread_row["provider_id"])

        if thread_row is not None:
            thread_provider = _s(thread_row["provider_id"])
            if resolved_provider and thread_provider and resolved_provider != thread_provider:
                return (
                    f"Provider message thread {resolved_thread_id} belongs to "
                    f"{thread_provider}, not {resolved_provider}."
                )
            resolved_provider = resolved_provider or thread_provider

        related_option = _s(option_id or bundle_id).strip()
        option_row = None
        if related_option:
            option_row = self._plan_row_for_option(resolved_request_id, related_option)
            if option_row is None:
                return (
                    f"Service option '{related_option}' is not registered for request "
                    f"{resolved_request_id}; refusing to queue provider message."
                )
            if not resolved_provider:
                resolved_provider = _s(option_row["provider_id"])

        if not resolved_provider:
            return "Provider message request is missing provider_id."

        if not self._provider_bound_to_request(resolved_request_id, resolved_provider):
            return (
                f"Provider '{resolved_provider}' is not bound to request "
                f"{resolved_request_id}; refusing to queue provider message."
            )

        if option_row is not None:
            option_provider = _s(option_row["provider_id"])
            if option_provider and option_provider != resolved_provider:
                return (
                    f"Service option '{related_option}' belongs to {option_provider}, "
                    f"not {resolved_provider}."
                )

        role_val = self._current_agent_role or "unknown"
        self.conn.execute(
            "INSERT INTO provider_threads "
            "(timestamp, request_id, provider_id, "
            "actor_role, role, message_type, purpose, "
            "bundle_id, option_id, template_id, message_id, "
            "content, authored_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (_now_iso(), resolved_request_id,
             resolved_provider,
             role_val, role_val,
             str(message_type or "follow_up").strip(),
             _s(purpose),
             _s(bundle_id), _s(option_id),
             _s(template_id), _s(resolved_thread_id or message_id),
             str(content or "").strip(),
             role_val),
        )
        self.conn.commit()
        return f"Provider message queued for {resolved_provider} on {resolved_request_id}."
