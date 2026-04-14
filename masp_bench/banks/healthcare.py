"""HealthcareBank — SQLite-backed stateful backend for healthcare encounter tasks."""

from __future__ import annotations

import inspect
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_SQL = """
CREATE TABLE cases (
    case_id TEXT PRIMARY KEY,
    family TEXT NOT NULL,
    source_benchmark TEXT NOT NULL,
    source_record TEXT NOT NULL,
    objective TEXT NOT NULL,
    demographics TEXT NOT NULL,
    opening_summary TEXT NOT NULL,
    correct_diagnosis TEXT NOT NULL,
    recommended_service TEXT NOT NULL,
    urgency TEXT NOT NULL
);

CREATE TABLE case_packets (
    case_id TEXT NOT NULL,
    audience TEXT NOT NULL,
    focus TEXT NOT NULL,
    context TEXT NOT NULL,
    PRIMARY KEY (case_id, audience)
);

CREATE TABLE case_histories (
    case_id TEXT NOT NULL,
    history_scope TEXT NOT NULL,
    content TEXT NOT NULL,
    PRIMARY KEY (case_id, history_scope)
);

CREATE TABLE physical_exams (
    case_id TEXT NOT NULL,
    exam_scope TEXT NOT NULL,
    findings TEXT NOT NULL,
    PRIMARY KEY (case_id, exam_scope)
);

CREATE TABLE lab_panels (
    case_id TEXT NOT NULL,
    panel_name TEXT NOT NULL,
    urgency TEXT NOT NULL,
    findings TEXT NOT NULL,
    PRIMARY KEY (case_id, panel_name)
);

CREATE TABLE imaging_studies (
    case_id TEXT NOT NULL,
    study_name TEXT NOT NULL,
    modality TEXT NOT NULL,
    findings TEXT NOT NULL,
    PRIMARY KEY (case_id, study_name)
);

CREATE TABLE pathology_reports (
    case_id TEXT NOT NULL,
    specimen_type TEXT NOT NULL,
    report_section TEXT NOT NULL,
    findings TEXT NOT NULL,
    PRIMARY KEY (case_id, specimen_type, report_section)
);

CREATE TABLE specialty_consults (
    case_id TEXT NOT NULL,
    specialty TEXT NOT NULL,
    default_question TEXT NOT NULL,
    impression TEXT NOT NULL,
    recommended_next_step TEXT NOT NULL,
    PRIMARY KEY (case_id, specialty)
);

CREATE TABLE assessments (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    case_id TEXT NOT NULL,
    assessment_type TEXT NOT NULL,
    content TEXT NOT NULL,
    authored_by TEXT NOT NULL
);

CREATE TABLE triage_recommendations (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    case_id TEXT NOT NULL,
    recommendation TEXT NOT NULL,
    justification TEXT NOT NULL,
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

    def _seed(self, seed_dir: Path) -> None:
        with open(seed_dir / "seed_cases.json") as f:
            seed = json.load(f)

        for case_id, payload in seed["cases"].items():
            self.conn.execute(
                "INSERT INTO cases (case_id, family, source_benchmark, source_record, objective, demographics, opening_summary, correct_diagnosis, recommended_service, urgency) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    case_id,
                    payload["family"],
                    payload["source_benchmark"],
                    payload["source_record"],
                    payload["objective"],
                    payload["demographics"],
                    payload["opening_summary"],
                    payload["correct_diagnosis"],
                    payload["recommended_service"],
                    payload["urgency"],
                ),
            )

            for audience, packet in payload.get("packets", {}).items():
                self.conn.execute(
                    "INSERT INTO case_packets (case_id, audience, focus, context) VALUES (?, ?, ?, ?)",
                    (case_id, audience, packet["focus"], packet["context"]),
                )

            for history_scope, content in payload.get("histories", {}).items():
                self.conn.execute(
                    "INSERT INTO case_histories (case_id, history_scope, content) VALUES (?, ?, ?)",
                    (case_id, history_scope, content),
                )

            for exam_scope, findings in payload.get("physical_exams", {}).items():
                self.conn.execute(
                    "INSERT INTO physical_exams (case_id, exam_scope, findings) VALUES (?, ?, ?)",
                    (case_id, exam_scope, findings),
                )

            for panel_name, panel in payload.get("lab_panels", {}).items():
                self.conn.execute(
                    "INSERT INTO lab_panels (case_id, panel_name, urgency, findings) VALUES (?, ?, ?, ?)",
                    (case_id, panel_name, panel["urgency"], panel["summary"]),
                )

            for study_name, study in payload.get("imaging_studies", {}).items():
                self.conn.execute(
                    "INSERT INTO imaging_studies (case_id, study_name, modality, findings) VALUES (?, ?, ?, ?)",
                    (case_id, study_name, study["modality"], study["summary"]),
                )

            for _report_key, report in payload.get("pathology_reports", {}).items():
                self.conn.execute(
                    "INSERT INTO pathology_reports (case_id, specimen_type, report_section, findings) VALUES (?, ?, ?, ?)",
                    (
                        case_id,
                        report["specimen_type"],
                        report["report_section"],
                        report["summary"],
                    ),
                )

            for specialty, consult in payload.get("specialty_consults", {}).items():
                self.conn.execute(
                    "INSERT INTO specialty_consults (case_id, specialty, default_question, impression, recommended_next_step) VALUES (?, ?, ?, ?, ?)",
                    (
                        case_id,
                        specialty,
                        consult["default_question"],
                        consult["impression"],
                        consult["recommended_next_step"],
                    ),
                )

        self.conn.commit()

    def set_current_agent(self, role: str | None) -> None:
        self._current_agent_role = role

    def apply_task_metadata(self, metadata: dict[str, Any]) -> None:
        case_id = str(metadata.get("case_id", "")).strip()
        self._active_case_id = case_id or None

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

    def _resolve_case_id(self, case_id: str = "") -> str:
        resolved = str(case_id or self._active_case_id or "").strip()
        return resolved

    def _case_row(self, case_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM cases WHERE case_id = ?",
            (case_id,),
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

    def tool_get_patient_case_packet(self, case_id: str = "", audience: str = "", **kwargs: Any) -> str:
        resolved_case_id = self._resolve_case_id(case_id)
        if not resolved_case_id:
            return "Case packet request is missing case_id."
        case_row = self._case_row(resolved_case_id)
        if case_row is None:
            return f"Case not found: {resolved_case_id}"

        audience_key = str(audience or self._current_agent_role or "care_coordinator").strip()
        packet_row = self.conn.execute(
            "SELECT * FROM case_packets WHERE case_id = ? AND audience = ?",
            (resolved_case_id, audience_key),
        ).fetchone()
        if packet_row is None:
            packet_row = self.conn.execute(
                "SELECT * FROM case_packets WHERE case_id = ? ORDER BY audience LIMIT 1",
                (resolved_case_id,),
            ).fetchone()

        lines = [
            f"Case Packet - {case_row['case_id']}",
            f"Family: {case_row['family']}",
            f"Objective: {case_row['objective']}",
            f"PATIENT-DEMOGRAPHICS: {case_row['demographics']}",
            f"Opening Summary: {case_row['opening_summary']}",
        ]
        if packet_row is not None:
            lines.extend(
                [
                    f"Audience: {packet_row['audience']}",
                    f"Audience Focus: {packet_row['focus']}",
                    f"Scope Note: {packet_row['context']}",
                ]
            )
        return "\n".join(lines)

    def tool_review_prior_history(self, case_id: str = "", history_scope: str = "", **kwargs: Any) -> str:
        resolved_case_id = self._resolve_case_id(case_id)
        if not resolved_case_id:
            return "History review request is missing case_id."

        rows = self._rows_for_case("case_histories", resolved_case_id, "history_scope")
        selected = self._filter_rows(rows, history_scope, fields=("history_scope", "content"))
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

    def tool_request_physical_exam(self, case_id: str = "", exam_scope: str = "", **kwargs: Any) -> str:
        resolved_case_id = self._resolve_case_id(case_id)
        if not resolved_case_id:
            return "Physical exam request is missing case_id."

        rows = self._rows_for_case("physical_exams", resolved_case_id, "exam_scope")
        selected = self._filter_rows(rows, exam_scope, fields=("exam_scope", "findings"))
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

    def tool_request_lab_panel(
        self,
        case_id: str = "",
        panel_name: str = "",
        urgency: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_case_id = self._resolve_case_id(case_id)
        if not resolved_case_id:
            return "Lab request is missing case_id."

        rows = self._rows_for_case("lab_panels", resolved_case_id, "panel_name")
        selected = self._filter_rows(rows, panel_name or urgency, fields=("panel_name", "urgency", "findings"))
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

    def tool_request_imaging_study(
        self,
        case_id: str = "",
        study_name: str = "",
        modality: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_case_id = self._resolve_case_id(case_id)
        if not resolved_case_id:
            return "Imaging request is missing case_id."

        rows = self._rows_for_case("imaging_studies", resolved_case_id, "study_name")
        selected = self._filter_rows(rows, study_name or modality, fields=("study_name", "modality", "findings"))
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
        self,
        case_id: str = "",
        specimen_type: str = "",
        report_section: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_case_id = self._resolve_case_id(case_id)
        if not resolved_case_id:
            return "Pathology request is missing case_id."

        rows = self._rows_for_case("pathology_reports", resolved_case_id, "specimen_type, report_section")
        selected = self._filter_rows(
            rows,
            specimen_type or report_section,
            fields=("specimen_type", "report_section", "findings"),
        )
        if not selected:
            available = ", ".join(row["specimen_type"] for row in rows) if rows else "(none)"
            return (
                f"Pathology Report - {resolved_case_id}\n"
                f"No pathology report matched '{specimen_type or report_section}'. Available reports: {available}"
            )

        lines = [
            f"Pathology Report - {resolved_case_id}",
            f"Requested Specimen: {specimen_type or 'all'}",
            f"Requested Section: {report_section or '(none)'}",
        ]
        for row in selected:
            lines.append(f"- {row['specimen_type']} [{row['report_section']}]: {row['findings']}")
        return "\n".join(lines)

    def tool_request_specialty_consult(
        self,
        case_id: str = "",
        specialty: str = "",
        consult_question: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_case_id = self._resolve_case_id(case_id)
        if not resolved_case_id:
            return "Specialty consult request is missing case_id."

        rows = self._rows_for_case("specialty_consults", resolved_case_id, "specialty")
        selected = self._filter_rows(rows, specialty, fields=("specialty", "default_question", "impression"))
        if not selected:
            available = ", ".join(row["specialty"] for row in rows) if rows else "(none)"
            return (
                f"Specialty Consult - {resolved_case_id}\n"
                f"No specialty consult matched '{specialty}'. Available consults: {available}"
            )

        row = selected[0]
        lines = [
            f"Specialty Consult - {resolved_case_id}",
            f"Specialty: {row['specialty']}",
            f"Question: {consult_question or row['default_question']}",
            f"Impression: {row['impression']}",
            f"Recommended Next Step: {row['recommended_next_step']}",
        ]
        return "\n".join(lines)

    def tool_record_clinical_assessment(
        self,
        case_id: str = "",
        assessment_type: str = "",
        content: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_case_id = self._resolve_case_id(case_id)
        if not resolved_case_id:
            return "Clinical assessment request is missing case_id."
        if self._case_row(resolved_case_id) is None:
            return f"Case not found: {resolved_case_id}"

        self.conn.execute(
            "INSERT INTO assessments (timestamp, case_id, assessment_type, content, authored_by) VALUES (?, ?, ?, ?, ?)",
            (
                _now_iso(),
                resolved_case_id,
                str(assessment_type or "working_assessment").strip(),
                str(content or "").strip(),
                self._current_agent_role or "unknown",
            ),
        )
        self.conn.commit()
        return (
            f"Clinical assessment saved for {resolved_case_id} "
            f"as {str(assessment_type or 'working_assessment').strip()}."
        )

    def tool_issue_triage_recommendation(
        self,
        case_id: str = "",
        recommendation: str = "",
        justification: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_case_id = self._resolve_case_id(case_id)
        if not resolved_case_id:
            return "Triage recommendation request is missing case_id."
        if self._case_row(resolved_case_id) is None:
            return f"Case not found: {resolved_case_id}"

        self.conn.execute(
            "INSERT INTO triage_recommendations (timestamp, case_id, recommendation, justification, authored_by) VALUES (?, ?, ?, ?, ?)",
            (
                _now_iso(),
                resolved_case_id,
                str(recommendation or "").strip(),
                str(justification or "").strip(),
                self._current_agent_role or "unknown",
            ),
        )
        self.conn.commit()
        return f"Triage recommendation saved for {resolved_case_id}."
