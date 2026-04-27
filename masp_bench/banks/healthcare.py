"""HealthcareBank — SQLite-backed stateful backend for healthcare encounter tasks.

Expanded for scaled tasks (hc-t7 through hc-t33).  Loads seed_cases.json and
records.json.  Schema includes every column referenced by sql_assert completion
checkpoints.
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
CREATE TABLE cases (
    case_id TEXT PRIMARY KEY,
    family TEXT NOT NULL DEFAULT '',
    source_benchmark TEXT NOT NULL DEFAULT '',
    source_record TEXT NOT NULL DEFAULT '',
    objective TEXT NOT NULL DEFAULT '',
    demographics TEXT NOT NULL DEFAULT '',
    opening_summary TEXT NOT NULL DEFAULT '',
    correct_diagnosis TEXT NOT NULL DEFAULT '',
    recommended_service TEXT NOT NULL DEFAULT '',
    urgency TEXT NOT NULL DEFAULT ''
);

CREATE TABLE case_histories (
    case_id TEXT NOT NULL,
    history_scope TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    record_status TEXT NOT NULL DEFAULT 'active',
    record_version TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (case_id, history_scope)
);

CREATE TABLE physical_exams (
    case_id TEXT NOT NULL,
    exam_scope TEXT NOT NULL,
    exam TEXT NOT NULL DEFAULT '',
    findings TEXT NOT NULL DEFAULT '',
    record_status TEXT NOT NULL DEFAULT 'active',
    PRIMARY KEY (case_id, exam_scope)
);

CREATE TABLE lab_panels (
    case_id TEXT NOT NULL,
    panel_name TEXT NOT NULL,
    panel TEXT NOT NULL DEFAULT '',
    urgency TEXT NOT NULL DEFAULT '',
    findings TEXT NOT NULL DEFAULT '',
    record_status TEXT NOT NULL DEFAULT 'active',
    PRIMARY KEY (case_id, panel_name)
);

CREATE TABLE imaging_studies (
    case_id TEXT NOT NULL,
    study_name TEXT NOT NULL,
    modality TEXT NOT NULL DEFAULT '',
    findings TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (case_id, study_name)
);

CREATE TABLE pathology_reports (
    case_id TEXT NOT NULL,
    specimen_type TEXT NOT NULL,
    report_section TEXT NOT NULL,
    findings TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (case_id, specimen_type, report_section)
);

CREATE TABLE specialty_consults (
    case_id TEXT NOT NULL,
    specialty TEXT NOT NULL,
    consult_type TEXT NOT NULL DEFAULT '',
    service TEXT NOT NULL DEFAULT '',
    consult_id TEXT NOT NULL DEFAULT '',
    default_question TEXT NOT NULL DEFAULT '',
    impression TEXT NOT NULL DEFAULT '',
    recommended_next_step TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (case_id, specialty)
);

CREATE TABLE assessments (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT '',
    case_id TEXT NOT NULL DEFAULT '',
    client_id TEXT NOT NULL DEFAULT '',
    assessment_type TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    authored_by TEXT NOT NULL DEFAULT ''
);

CREATE TABLE triage_recommendations (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT '',
    case_id TEXT NOT NULL DEFAULT '',
    client_id TEXT NOT NULL DEFAULT '',
    recommendation TEXT NOT NULL DEFAULT '',
    urgency TEXT NOT NULL DEFAULT '',
    justification TEXT NOT NULL DEFAULT '',
    authored_by TEXT NOT NULL DEFAULT ''
);

CREATE TABLE audit_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT '',
    tool TEXT NOT NULL DEFAULT '',
    tool_name TEXT NOT NULL DEFAULT '',
    args_json TEXT NOT NULL DEFAULT '{}',
    args TEXT NOT NULL DEFAULT '{}',
    agent_role TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT '',
    case_id TEXT NOT NULL DEFAULT '',
    resource_name TEXT NOT NULL DEFAULT '',
    resource_table TEXT NOT NULL DEFAULT '',
    target_resource TEXT NOT NULL DEFAULT '',
    exam_type TEXT NOT NULL DEFAULT '',
    panel_name TEXT NOT NULL DEFAULT '',
    panel TEXT NOT NULL DEFAULT '',
    consult_id TEXT NOT NULL DEFAULT '',
    service TEXT NOT NULL DEFAULT '',
    specialty TEXT NOT NULL DEFAULT '',
    record_status TEXT NOT NULL DEFAULT '',
    record_version TEXT NOT NULL DEFAULT ''
);
"""

_SEED_TS = "2000-01-01T00:00:00Z"

# Map tool_name → resource table for structured audit_log writes.
_TOOL_TABLE_MAP = {
    "review_prior_history": "case_histories",
    "order_physical_exam": "physical_exams",
    "order_lab_panel": "lab_panels",
    "order_imaging_study": "imaging_studies",
    "request_pathology_report": "pathology_reports",
    "order_specialty_consult": "specialty_consults",
    "record_clinical_assessment": "assessments",
    "issue_triage_recommendation": "triage_recommendations",
    "view_exam_findings": "physical_exams",
    "view_lab_results": "lab_panels",
    "view_imaging_report": "imaging_studies",
    "view_consult_opinion": "specialty_consults",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _s(val: Any) -> str:
    return str(val) if val is not None else ""


def _parse_csv(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


# ---------------------------------------------------------------------------
# Bank
# ---------------------------------------------------------------------------

class HealthcareBank:
    """SQLite-backed stateful backend for healthcare encounter tools."""

    def __init__(self, seed_dir: Path, db_path: str = ":memory:"):
        self.seed_dir = seed_dir
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA_SQL)
        self._current_agent_role: str | None = None
        self._active_case_id: str | None = None
        self._seed(seed_dir)

    # -----------------------------------------------------------------------
    # Seed loading
    # -----------------------------------------------------------------------

    def _seed(self, seed_dir: Path) -> None:
        self._seed_cases(seed_dir)
        rec_path = seed_dir / "records.json"
        if rec_path.exists():
            self._seed_records(seed_dir)
        self.conn.commit()

    def _seed_cases(self, seed_dir: Path) -> None:
        with open(seed_dir / "seed_cases.json") as f:
            seed = json.load(f)

        for case_id, payload in seed.get("cases", {}).items():
            self.conn.execute(
                "INSERT OR IGNORE INTO cases "
                "(case_id, family, source_benchmark, source_record, objective, "
                "demographics, opening_summary, correct_diagnosis, "
                "recommended_service, urgency) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (case_id,
                 _s(payload.get("family")),
                 _s(payload.get("source_benchmark")),
                 _s(payload.get("source_record")),
                 _s(payload.get("objective")),
                 _s(payload.get("demographics")),
                 _s(payload.get("opening_summary")),
                 _s(payload.get("correct_diagnosis")),
                 _s(payload.get("recommended_service")),
                 _s(payload.get("urgency"))),
            )

            for scope, content in payload.get("histories", {}).items():
                self.conn.execute(
                    "INSERT OR IGNORE INTO case_histories "
                    "(case_id, history_scope, content) VALUES (?, ?, ?)",
                    (case_id, scope, _s(content)),
                )

            for scope, findings in payload.get("physical_exams", {}).items():
                self.conn.execute(
                    "INSERT OR IGNORE INTO physical_exams "
                    "(case_id, exam_scope, exam, findings) VALUES (?, ?, ?, ?)",
                    (case_id, scope, scope, _s(findings)),
                )

            for pname, panel in payload.get("lab_panels", {}).items():
                self.conn.execute(
                    "INSERT OR IGNORE INTO lab_panels "
                    "(case_id, panel_name, panel, urgency, findings) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (case_id, pname, pname,
                     _s(panel.get("urgency")),
                     _s(panel.get("summary"))),
                )

            for sname, study in payload.get("imaging_studies", {}).items():
                self.conn.execute(
                    "INSERT OR IGNORE INTO imaging_studies "
                    "(case_id, study_name, modality, findings) VALUES (?, ?, ?, ?)",
                    (case_id, sname,
                     _s(study.get("modality")),
                     _s(study.get("summary"))),
                )

            for _rk, report in payload.get("pathology_reports", {}).items():
                self.conn.execute(
                    "INSERT OR IGNORE INTO pathology_reports "
                    "(case_id, specimen_type, report_section, findings) "
                    "VALUES (?, ?, ?, ?)",
                    (case_id,
                     _s(report.get("specimen_type")),
                     _s(report.get("report_section")),
                     _s(report.get("summary"))),
                )

            for spec, consult in payload.get("specialty_consults", {}).items():
                consult_id = f"CONS-{case_id}-{spec.upper().replace(' ', '_')[:10]}"
                self.conn.execute(
                    "INSERT OR IGNORE INTO specialty_consults "
                    "(case_id, specialty, consult_type, service, consult_id, "
                    "default_question, impression, recommended_next_step) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (case_id, spec, spec, spec, consult_id,
                     _s(consult.get("default_question")),
                     _s(consult.get("impression")),
                     _s(consult.get("recommended_next_step"))),
                )

    def _seed_records(self, seed_dir: Path) -> None:
        """Load supplementary records (decoy versions, attachments, metadata)."""
        with open(seed_dir / "records.json") as f:
            recs = json.load(f)

        for key, val in recs.items():
            if not isinstance(val, dict):
                continue

            case_id = _s(val.get("case_id", ""))
            rec_status = _s(val.get("record_status", "active"))
            rec_version = _s(val.get("record_version", val.get("version", "")))

            # Medication records → case_histories
            if "medication" in key.lower() or "active_medication" in key.lower():
                if case_id:
                    scope = f"medication:{rec_version or 'current'}"
                    content = json.dumps(val, default=str)
                    self.conn.execute(
                        "INSERT OR IGNORE INTO case_histories "
                        "(case_id, history_scope, content, record_status, record_version) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (case_id, scope, content, rec_status, rec_version),
                    )

            # Portal messages → case_histories
            elif "portal_message" in key.lower():
                if case_id:
                    scope = f"portal_message:{rec_version or key}"
                    content = _s(val.get("message_text", json.dumps(val, default=str)))
                    self.conn.execute(
                        "INSERT OR IGNORE INTO case_histories "
                        "(case_id, history_scope, content, record_status, record_version) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (case_id, scope, content, rec_status, rec_version),
                    )

            # Telehealth records → case_histories
            elif "telehealth" in key.lower():
                if case_id:
                    scope = f"telehealth:{rec_version or key}"
                    content = json.dumps(val, default=str)
                    self.conn.execute(
                        "INSERT OR IGNORE INTO case_histories "
                        "(case_id, history_scope, content, record_status, record_version) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (case_id, scope, content, rec_status, rec_version),
                    )

            # Exam records → physical_exams
            elif key.startswith("general_exam_") or key.startswith("EXAMAUTH-"):
                if case_id:
                    scope = _s(val.get("exam_type", key))
                    findings = _s(val.get("exam_text",
                                  val.get("authorized_finding",
                                  json.dumps(val, default=str))))
                    self.conn.execute(
                        "INSERT OR IGNORE INTO physical_exams "
                        "(case_id, exam_scope, exam, findings, record_status) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (case_id, scope, scope, findings, rec_status),
                    )

            # Lab records → lab_panels
            elif key.startswith("lab_panel_") or key.startswith("LABAUTH-"):
                if case_id:
                    pname = _s(val.get("panel_name", key))
                    findings = _s(val.get("panel_text",
                                  val.get("authorized_result",
                                  json.dumps(val, default=str))))
                    self.conn.execute(
                        "INSERT OR IGNORE INTO lab_panels "
                        "(case_id, panel_name, panel, urgency, findings, record_status) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (case_id, pname, pname, "", findings, rec_status),
                    )

            # Consult records → specialty_consults
            elif key.startswith("CONSULT-") or "consult" in key.lower():
                if case_id and "specialty" in val:
                    spec = _s(val.get("specialty"))
                    self.conn.execute(
                        "INSERT OR IGNORE INTO specialty_consults "
                        "(case_id, specialty, consult_type, service, consult_id, "
                        "default_question, impression, recommended_next_step) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (case_id, spec, spec, spec, key,
                         _s(val.get("default_question", "")),
                         _s(val.get("impression",
                                    val.get("consult_text", ""))),
                         _s(val.get("recommended_next_step", ""))),
                    )

            # History records → case_histories
            elif key.startswith("HISTORY-") or "prior_history" in key.lower():
                if case_id:
                    scope = _s(val.get("kind", key))
                    content = _s(val.get("summary",
                                 val.get("psychiatric_history",
                                 json.dumps(val, default=str))))
                    self.conn.execute(
                        "INSERT OR IGNORE INTO case_histories "
                        "(case_id, history_scope, content, record_status, record_version) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (case_id, scope, content, rec_status, rec_version),
                    )

    # -----------------------------------------------------------------------
    # Public interface
    # -----------------------------------------------------------------------

    def set_current_agent(self, role: str | None) -> None:
        self._current_agent_role = role

    def apply_task_metadata(self, metadata: dict[str, Any]) -> None:
        case_id = str(metadata.get("case_id", "")).strip()
        self._active_case_id = case_id or None

    def dispatch(self, tool_name: str, args: dict[str, Any]) -> str:
        role_val = self._current_agent_role or ""
        case_id = _s(args.get("case_id", ""))

        # Derive resource_name from the primary scope/name arg
        resource_name = _s(
            args.get("exam_scope") or args.get("panel_name") or
            args.get("study_name") or args.get("specimen_type") or
            args.get("specialty") or args.get("history_scope") or
            args.get("option_id") or ""
        )
        resource_table = _TOOL_TABLE_MAP.get(tool_name, "")

        try:
            args_str = json.dumps(args, default=str)
            self.conn.execute(
                "INSERT INTO audit_log "
                "(timestamp, tool, tool_name, args_json, args, "
                "agent_role, role, case_id, "
                "resource_name, resource_table, target_resource, "
                "exam_type, panel_name, panel, "
                "consult_id, service, specialty, "
                "record_status, record_version) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_now_iso(), tool_name, tool_name,
                 args_str, args_str,
                 role_val, role_val, case_id,
                 resource_name, resource_table, resource_name,
                 _s(args.get("exam_scope", args.get("exam_type", ""))),
                 _s(args.get("panel_name", "")),
                 _s(args.get("panel_name", args.get("panel", ""))),
                 _s(args.get("consult_id", "")),
                 _s(args.get("service", args.get("specialty", ""))),
                 _s(args.get("specialty", "")),
                 _s(args.get("record_status", "")),
                 _s(args.get("record_version", ""))),
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
    # Internal resolution helpers
    # -----------------------------------------------------------------------

    def _resolve_case_id(self, case_id: str = "") -> str:
        return str(case_id or self._active_case_id or "").strip()

    def _case_row(self, case_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM cases WHERE case_id = ?", (case_id,),
        ).fetchone()

    def _rows_for_case(self, table: str, case_id: str, order_by: str) -> list[sqlite3.Row]:
        query = f"SELECT * FROM {table} WHERE case_id = ? ORDER BY {order_by}"
        return list(self.conn.execute(query, (case_id,)).fetchall())

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
        normalized_queries = [_norm(q) for q in queries
                              if _norm(q) not in {"all", "full", "complete"}]
        if not normalized_queries:
            return rows
        selected: list[sqlite3.Row] = []
        for row in rows:
            haystacks = [_norm(row[field]) for field in fields]
            if any(q in haystack or haystack in q
                   for q in normalized_queries for haystack in haystacks):
                selected.append(row)
        return selected

    # -----------------------------------------------------------------------
    # Tool handlers — READ
    # -----------------------------------------------------------------------

    def tool_review_prior_history(self, case_id: str = "", history_scope: str = "", **kwargs: Any) -> str:
        resolved_case_id = self._resolve_case_id(case_id)
        if not resolved_case_id:
            return "History review request is missing case_id."

        rows = self._rows_for_case("case_histories", resolved_case_id, "history_scope")
        selected = self._filter_rows(rows, history_scope, fields=("history_scope",))
        if not selected:
            available = ", ".join(row["history_scope"] for row in rows) if rows else "(none)"
            return (
                f"History Review - {resolved_case_id}\n"
                f"No history matched scope '{history_scope}'. Available scopes: {available}"
            )

        lines = [
            f"History Review - {resolved_case_id}",
            f"Requested Scope: {history_scope or 'all'}",
        ]
        for row in selected:
            lines.append(f"- {row['history_scope']}: {row['content']}")
        return "\n".join(lines)

    def tool_order_physical_exam(self, case_id: str = "", exam_scope: str = "", **kwargs: Any) -> str:
        resolved_case_id = self._resolve_case_id(case_id)
        if not resolved_case_id:
            return "Physical exam request is missing case_id."

        rows = self._rows_for_case("physical_exams", resolved_case_id, "exam_scope")
        selected = self._filter_rows(rows, exam_scope, fields=("exam_scope",))
        if not selected:
            available = ", ".join(row["exam_scope"] for row in rows) if rows else "(none)"
            return (
                f"Physical Exam - {resolved_case_id}\n"
                f"No physical exam matched scope '{exam_scope}'. Available scopes: {available}"
            )

        lines = [
            f"Physical Exam - {resolved_case_id}",
            f"Requested Scope: {exam_scope or 'all'}",
        ]
        for row in selected:
            lines.append(f"- {row['exam_scope']}: {row['findings']}")
        return "\n".join(lines)

    def tool_order_lab_panel(
        self, case_id: str = "", panel_name: str = "",
        urgency: str = "", **kwargs: Any,
    ) -> str:
        resolved_case_id = self._resolve_case_id(case_id)
        if not resolved_case_id:
            return "Lab request is missing case_id."

        rows = self._rows_for_case("lab_panels", resolved_case_id, "panel_name")
        selected = self._filter_rows(rows, panel_name or urgency,
                                     fields=("panel_name", "urgency"))
        if not selected:
            available = ", ".join(row["panel_name"] for row in rows) if rows else "(none)"
            return (
                f"Lab Panel - {resolved_case_id}\n"
                f"No lab panel matched '{panel_name or urgency}'. Available panels: {available}"
            )

        lines = [
            f"Lab Panel - {resolved_case_id}",
            f"Requested Panel: {panel_name or 'all'}",
            f"Urgency Hint: {urgency or '(none)'}",
        ]
        for row in selected:
            lines.append(f"- {row['panel_name']} [{row['urgency']}]: {row['findings']}")
        return "\n".join(lines)

    def tool_order_imaging_study(
        self, case_id: str = "", study_name: str = "",
        modality: str = "", **kwargs: Any,
    ) -> str:
        resolved_case_id = self._resolve_case_id(case_id)
        if not resolved_case_id:
            return "Imaging request is missing case_id."

        rows = self._rows_for_case("imaging_studies", resolved_case_id, "study_name")
        selected = self._filter_rows(rows, study_name or modality,
                                     fields=("study_name", "modality"))
        if not selected:
            available = ", ".join(row["study_name"] for row in rows) if rows else "(none)"
            return (
                f"Imaging Study - {resolved_case_id}\n"
                f"No imaging study matched '{study_name or modality}'. Available studies: {available}"
            )

        lines = [
            f"Imaging Study - {resolved_case_id}",
            f"Requested Study: {study_name or 'all'}",
            f"Modality Hint: {modality or '(none)'}",
        ]
        for row in selected:
            lines.append(f"- {row['study_name']} [{row['modality']}]: {row['findings']}")
        return "\n".join(lines)

    def tool_request_pathology_report(
        self, case_id: str = "", specimen_type: str = "",
        report_section: str = "", **kwargs: Any,
    ) -> str:
        resolved_case_id = self._resolve_case_id(case_id)
        if not resolved_case_id:
            return "Pathology request is missing case_id."

        rows = self._rows_for_case("pathology_reports", resolved_case_id,
                                   "specimen_type, report_section")
        selected = self._filter_rows(
            rows, specimen_type or report_section,
            fields=("specimen_type", "report_section"),
        )
        if not selected:
            available = ", ".join(row["specimen_type"] for row in rows) if rows else "(none)"
            return (
                f"Pathology Report - {resolved_case_id}\n"
                f"No pathology report matched '{specimen_type or report_section}'. "
                f"Available reports: {available}"
            )

        lines = [
            f"Pathology Report - {resolved_case_id}",
            f"Requested Specimen: {specimen_type or 'all'}",
            f"Requested Section: {report_section or '(none)'}",
        ]
        for row in selected:
            lines.append(f"- {row['specimen_type']} [{row['report_section']}]: {row['findings']}")
        return "\n".join(lines)

    def tool_order_specialty_consult(
        self, case_id: str = "", specialty: str = "",
        consult_question: str = "", **kwargs: Any,
    ) -> str:
        resolved_case_id = self._resolve_case_id(case_id)
        if not resolved_case_id:
            return "Specialty consult request is missing case_id."

        rows = self._rows_for_case("specialty_consults", resolved_case_id, "specialty")
        selected = self._filter_rows(rows, specialty,
                                     fields=("specialty", "default_question", "impression"))
        if not selected:
            available = ", ".join(row["specialty"] for row in rows) if rows else "(none)"
            return (
                f"Specialty Consult - {resolved_case_id}\n"
                f"No specialty consult matched '{specialty}'. Available consults: {available}"
            )

        row = selected[0]
        return "\n".join([
            f"Specialty Consult - {resolved_case_id}",
            f"Specialty: {row['specialty']}",
            f"Consult ID: {row['consult_id']}",
            f"Question: {consult_question or row['default_question']}",
            f"Impression: {row['impression']}",
            f"Recommended Next Step: {row['recommended_next_step']}",
        ])

    # -----------------------------------------------------------------------
    # Tool handlers — MUTATE
    # -----------------------------------------------------------------------

    def tool_record_clinical_assessment(
        self, case_id: str = "", assessment_type: str = "",
        content: str = "", client_id: str = "", **kwargs: Any,
    ) -> str:
        resolved_case_id = self._resolve_case_id(case_id)
        if not resolved_case_id:
            return "Clinical assessment request is missing case_id."
        if self._case_row(resolved_case_id) is None:
            return f"Case not found: {resolved_case_id}"

        self.conn.execute(
            "INSERT INTO assessments "
            "(timestamp, case_id, client_id, assessment_type, content, authored_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (_now_iso(), resolved_case_id,
             _s(client_id),
             str(assessment_type or "working_assessment").strip(),
             str(content or "").strip(),
             self._current_agent_role or "unknown"),
        )
        self.conn.commit()
        return (
            f"Clinical assessment saved for {resolved_case_id} "
            f"as {str(assessment_type or 'working_assessment').strip()}."
        )

    def tool_issue_triage_recommendation(
        self, case_id: str = "", recommendation: str = "",
        justification: str = "", urgency: str = "",
        client_id: str = "", **kwargs: Any,
    ) -> str:
        resolved_case_id = self._resolve_case_id(case_id)
        if not resolved_case_id:
            return "Triage recommendation request is missing case_id."
        if self._case_row(resolved_case_id) is None:
            return f"Case not found: {resolved_case_id}"

        self.conn.execute(
            "INSERT INTO triage_recommendations "
            "(timestamp, case_id, client_id, recommendation, urgency, "
            "justification, authored_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_now_iso(), resolved_case_id,
             _s(client_id),
             str(recommendation or "").strip(),
             _s(urgency),
             str(justification or "").strip(),
             self._current_agent_role or "unknown"),
        )
        self.conn.commit()
        return f"Triage recommendation saved for {resolved_case_id}."

    # ------------------------------------------------------------------
    # View-side aliases (read-only, same logic as order_* handlers)
    # ------------------------------------------------------------------

    def tool_view_exam_findings(self, case_id: str = "", exam_scope: str = "", **kwargs) -> str:
        """View physical exam findings (read-only alias for order_physical_exam)."""
        return self.tool_order_physical_exam(case_id=case_id, exam_scope=exam_scope)

    def tool_view_lab_results(self, case_id: str = "", panel_name: str = "", **kwargs) -> str:
        """View lab results (read-only alias for order_lab_panel)."""
        return self.tool_order_lab_panel(case_id=case_id, panel_name=panel_name)

    def tool_view_imaging_report(self, case_id: str = "", study_name: str = "", **kwargs) -> str:
        """View imaging report (read-only alias for order_imaging_study)."""
        return self.tool_order_imaging_study(case_id=case_id, study_name=study_name)

    def tool_view_consult_opinion(self, case_id: str = "", specialty: str = "", **kwargs) -> str:
        """View specialist opinion (read-only alias for order_specialty_consult)."""
        return self.tool_order_specialty_consult(case_id=case_id, specialty=specialty)
