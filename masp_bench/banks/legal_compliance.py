"""LegalComplianceBank — SQLite-backed stateful backend for legal tasks."""

from __future__ import annotations

import inspect
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_SQL = """
CREATE TABLE matters (
    matter_id TEXT PRIMARY KEY,
    family TEXT NOT NULL,
    source_benchmark TEXT NOT NULL,
    objective TEXT NOT NULL,
    deadline TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE matter_packets (
    matter_id TEXT NOT NULL,
    audience TEXT NOT NULL,
    focus TEXT NOT NULL,
    context TEXT NOT NULL,
    PRIMARY KEY (matter_id, audience)
);

CREATE TABLE matter_documents (
    document_id TEXT PRIMARY KEY,
    matter_id TEXT NOT NULL,
    document_type TEXT NOT NULL,
    section_scope TEXT NOT NULL,
    content TEXT NOT NULL
);

CREATE TABLE evidence_artifacts (
    artifact_id TEXT PRIMARY KEY,
    matter_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    verification_note TEXT NOT NULL
);

CREATE TABLE timeline_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    matter_id TEXT NOT NULL,
    event_date TEXT NOT NULL,
    event_label TEXT NOT NULL,
    details TEXT NOT NULL
);

CREATE TABLE exposure_models (
    matter_id TEXT NOT NULL,
    exposure_type TEXT NOT NULL,
    estimate_low REAL NOT NULL,
    estimate_high REAL NOT NULL,
    summary TEXT NOT NULL,
    PRIMARY KEY (matter_id, exposure_type)
);

CREATE TABLE legal_analyses (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    matter_id TEXT NOT NULL,
    analysis_type TEXT NOT NULL,
    source_bundle TEXT NOT NULL,
    content TEXT NOT NULL,
    authored_by TEXT NOT NULL
);

CREATE TABLE prepared_actions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    matter_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    source_bundle TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL,
    authored_by TEXT NOT NULL
);

CREATE TABLE executed_actions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    matter_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    action_ref TEXT NOT NULL,
    execution_note TEXT NOT NULL,
    status TEXT NOT NULL,
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


class LegalComplianceBank:
    """SQLite-backed stateful backend for legal/compliance workflows."""

    def __init__(self, seed_dir: Path, db_path: str = ":memory:"):
        self.seed_dir = seed_dir
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA_SQL)
        self._current_agent_role: str | None = None
        self._active_matter_id: str | None = None
        self._seed(seed_dir)

    def _seed(self, seed_dir: Path) -> None:
        with open(seed_dir / "seed_matters.json") as f:
            seed = json.load(f)

        for matter_id, payload in seed["matters"].items():
            self.conn.execute(
                "INSERT INTO matters (matter_id, family, source_benchmark, objective, deadline, status) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    matter_id,
                    payload["family"],
                    payload["source_benchmark"],
                    payload["objective"],
                    payload["deadline"],
                    payload.get("status", "open"),
                ),
            )

            for audience, packet in payload.get("packets", {}).items():
                self.conn.execute(
                    "INSERT INTO matter_packets (matter_id, audience, focus, context) VALUES (?, ?, ?, ?)",
                    (matter_id, audience, packet["focus"], packet["context"]),
                )

            for document_id, doc in payload.get("documents", {}).items():
                self.conn.execute(
                    "INSERT INTO matter_documents (document_id, matter_id, document_type, section_scope, content) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        document_id,
                        matter_id,
                        doc["document_type"],
                        doc["section_scope"],
                        doc["content"],
                    ),
                )

            for artifact_id, artifact in payload.get("evidence_artifacts", {}).items():
                self.conn.execute(
                    "INSERT INTO evidence_artifacts (artifact_id, matter_id, artifact_type, summary, verification_note) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        artifact_id,
                        matter_id,
                        artifact["artifact_type"],
                        artifact["summary"],
                        artifact["verification_note"],
                    ),
                )

            for event in payload.get("timeline_events", []):
                self.conn.execute(
                    "INSERT INTO timeline_events (matter_id, event_date, event_label, details) VALUES (?, ?, ?, ?)",
                    (matter_id, event["event_date"], event["event_label"], event["details"]),
                )

            for exposure_type, exposure in payload.get("exposure_models", {}).items():
                self.conn.execute(
                    "INSERT INTO exposure_models (matter_id, exposure_type, estimate_low, estimate_high, summary) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        matter_id,
                        exposure_type,
                        float(exposure["estimate_low"]),
                        float(exposure["estimate_high"]),
                        exposure["summary"],
                    ),
                )

        self.conn.commit()

    def set_current_agent(self, role: str | None) -> None:
        self._current_agent_role = role

    def apply_task_metadata(self, metadata: dict[str, Any]) -> None:
        matter_id = str(metadata.get("matter_id", "")).strip()
        self._active_matter_id = matter_id or None

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

    def _resolve_matter_id(self, matter_id: str = "") -> str:
        return str(matter_id or self._active_matter_id or "").strip()

    def _matter_row(self, matter_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM matters WHERE matter_id = ?",
            (matter_id,),
        ).fetchone()

    def _rows_for_matter(self, table: str, matter_id: str, order_by: str) -> list[sqlite3.Row]:
        query = f"SELECT * FROM {table} WHERE matter_id = ? ORDER BY {order_by}"
        return list(self.conn.execute(query, (matter_id,)).fetchall())

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

    def tool_get_matter_packet(
        self,
        matter_id: str = "",
        packet_scope: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_matter_id = self._resolve_matter_id(matter_id)
        if not resolved_matter_id:
            return "Matter packet request is missing matter_id."

        matter_row = self._matter_row(resolved_matter_id)
        if matter_row is None:
            return f"Matter not found: {resolved_matter_id}"

        audience_key = str(self._current_agent_role or "legal_matter_lead").strip()
        packet_row = self.conn.execute(
            "SELECT * FROM matter_packets WHERE matter_id = ? AND audience = ?",
            (resolved_matter_id, audience_key),
        ).fetchone()

        lines = [
            f"Matter Packet - {resolved_matter_id}",
            f"Family: {matter_row['family']}",
            f"Objective: {matter_row['objective']}",
            f"Deadline: {matter_row['deadline']}",
            f"Requested Scope: {packet_scope or 'default'}",
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

    def tool_review_matter_document(
        self,
        matter_id: str = "",
        document_id: str = "",
        section_scope: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_matter_id = self._resolve_matter_id(matter_id)
        if not resolved_matter_id:
            return "Matter document request is missing matter_id."

        rows = self._rows_for_matter("matter_documents", resolved_matter_id, "document_type, document_id")
        query = ",".join(part for part in [document_id, section_scope] if str(part).strip())
        selected = self._filter_rows(
            rows,
            query,
            fields=("document_id", "document_type", "section_scope", "content"),
        )
        if not selected:
            available = ", ".join(row["document_id"] for row in rows) if rows else "(none)"
            return (
                f"Matter Document - {resolved_matter_id}\n"
                f"No document matched '{query}'. Available documents: {available}"
            )

        lines = [
            f"Matter Document - {resolved_matter_id}",
            f"Requested Scope: {section_scope or document_id or 'all'}",
        ]
        for row in selected:
            lines.append(
                f"- {row['document_id']} [{row['document_type']} / {row['section_scope']}]: {row['content']}"
            )
        return "\n".join(lines)

    def tool_verify_evidence_artifact(
        self,
        matter_id: str = "",
        artifact_id: str = "",
        verification_goal: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_matter_id = self._resolve_matter_id(matter_id)
        if not resolved_matter_id:
            return "Evidence verification request is missing matter_id."

        rows = self._rows_for_matter("evidence_artifacts", resolved_matter_id, "artifact_type, artifact_id")
        selected = self._filter_rows(
            rows,
            ",".join(part for part in [artifact_id, verification_goal] if str(part).strip()),
            fields=("artifact_id", "artifact_type", "summary", "verification_note"),
        )
        if not selected:
            available = ", ".join(row["artifact_id"] for row in rows) if rows else "(none)"
            return (
                f"Evidence Artifact - {resolved_matter_id}\n"
                f"No artifact matched '{artifact_id or verification_goal}'. Available artifacts: {available}"
            )

        lines = [
            f"Evidence Artifact - {resolved_matter_id}",
            f"Verification Goal: {verification_goal or 'default'}",
        ]
        for row in selected:
            lines.append(f"- {row['artifact_id']} [{row['artifact_type']}]: {row['summary']}")
            lines.append(f"  Verification: {row['verification_note']}")
        return "\n".join(lines)

    def tool_compare_matter_timeline(
        self,
        matter_id: str = "",
        timeline_scope: str = "",
        comparison_goal: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_matter_id = self._resolve_matter_id(matter_id)
        if not resolved_matter_id:
            return "Timeline comparison request is missing matter_id."

        rows = self._rows_for_matter("timeline_events", resolved_matter_id, "event_date, seq")
        selected = self._filter_rows(
            rows,
            ",".join(part for part in [timeline_scope, comparison_goal] if str(part).strip()),
            fields=("event_label", "details"),
        )
        if not selected:
            selected = rows

        lines = [
            f"Timeline Comparison - {resolved_matter_id}",
            f"Timeline Scope: {timeline_scope or 'all'}",
            f"Comparison Goal: {comparison_goal or 'sequence review'}",
        ]
        for row in selected:
            lines.append(f"- {row['event_date']} | {row['event_label']}: {row['details']}")
        return "\n".join(lines)

    def tool_compute_exposure_estimate(
        self,
        matter_id: str = "",
        exposure_type: str = "",
        input_scope: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_matter_id = self._resolve_matter_id(matter_id)
        if not resolved_matter_id:
            return "Exposure estimate request is missing matter_id."

        resolved_exposure_type = str(exposure_type or "").strip()
        if not resolved_exposure_type:
            row = self.conn.execute(
                "SELECT exposure_type FROM exposure_models WHERE matter_id = ? ORDER BY exposure_type LIMIT 1",
                (resolved_matter_id,),
            ).fetchone()
            resolved_exposure_type = str(row["exposure_type"]).strip() if row is not None else ""
        if not resolved_exposure_type:
            return f"No exposure model available for {resolved_matter_id}."

        row = self.conn.execute(
            "SELECT * FROM exposure_models WHERE matter_id = ? AND exposure_type = ?",
            (resolved_matter_id, resolved_exposure_type),
        ).fetchone()
        if row is None:
            return f"Exposure model not found: {resolved_exposure_type}"

        return "\n".join(
            [
                f"Exposure Estimate - {resolved_matter_id}",
                f"Exposure Type: {resolved_exposure_type}",
                f"Input Scope: {input_scope or 'default'}",
                f"Range: ${float(row['estimate_low']):,.0f} to ${float(row['estimate_high']):,.0f}",
                f"Summary: {row['summary']}",
            ]
        )

    def tool_draft_legal_analysis(
        self,
        matter_id: str = "",
        analysis_type: str = "",
        source_bundle: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_matter_id = self._resolve_matter_id(matter_id)
        if not resolved_matter_id:
            return "Legal analysis request is missing matter_id."

        analysis_kind = str(analysis_type or "issue_memo").strip()
        content = (
            f"Draft {analysis_kind} for {resolved_matter_id} using {source_bundle or 'default bundle'}. "
            f"Keep privileged details scoped."
        )
        self.conn.execute(
            "INSERT INTO legal_analyses (timestamp, matter_id, analysis_type, source_bundle, content, authored_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                _now_iso(),
                resolved_matter_id,
                analysis_kind,
                str(source_bundle or "").strip(),
                content,
                self._current_agent_role or "unknown",
            ),
        )
        self.conn.commit()
        return (
            f"Legal analysis drafted for {resolved_matter_id} as {analysis_kind}."
        )

    def tool_prepare_legal_action(
        self,
        matter_id: str = "",
        action_type: str = "",
        source_bundle: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_matter_id = self._resolve_matter_id(matter_id)
        if not resolved_matter_id:
            return "Legal action preparation request is missing matter_id."

        prepared_type = str(action_type or "demand_letter").strip()
        content = (
            f"FILING-STRATEGY: Prepare {prepared_type} for {resolved_matter_id} "
            f"using {source_bundle or 'default source bundle'}."
        )
        self.conn.execute(
            "INSERT INTO prepared_actions (timestamp, matter_id, action_type, source_bundle, content, status, authored_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                _now_iso(),
                resolved_matter_id,
                prepared_type,
                str(source_bundle or "").strip(),
                content,
                "prepared",
                self._current_agent_role or "unknown",
            ),
        )
        self.conn.commit()
        return (
            f"Prepared legal action '{prepared_type}' for {resolved_matter_id}."
        )

    def tool_execute_legal_action(
        self,
        matter_id: str = "",
        action_type: str = "",
        action_ref: str = "",
        execution_note: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_matter_id = self._resolve_matter_id(matter_id)
        if not resolved_matter_id:
            return "Legal action execution request is missing matter_id."

        execution_type = str(action_type or "file").strip()
        new_status = "executed"
        if "hold" in _norm(execution_type):
            new_status = "held"
        elif "preservation" in _norm(execution_type):
            new_status = "preservation_filed"
        elif "notice" in _norm(execution_type):
            new_status = "notice_ready"

        self.conn.execute(
            "INSERT INTO executed_actions (timestamp, matter_id, action_type, action_ref, execution_note, status, authored_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                _now_iso(),
                resolved_matter_id,
                execution_type,
                str(action_ref or "").strip(),
                str(execution_note or "").strip(),
                new_status,
                self._current_agent_role or "unknown",
            ),
        )
        self.conn.execute(
            "UPDATE matters SET status = ? WHERE matter_id = ?",
            (new_status, resolved_matter_id),
        )
        self.conn.commit()
        return (
            f"Executed legal action '{execution_type}' for {resolved_matter_id}."
        )
