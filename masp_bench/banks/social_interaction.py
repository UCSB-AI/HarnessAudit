"""SocialInteractionBank — SQLite-backed stateful backend for social tasks."""

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
    objective TEXT NOT NULL,
    publication_threshold TEXT NOT NULL,
    anonymity_preference TEXT NOT NULL,
    budget_ceiling REAL NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE case_packets (
    case_id TEXT NOT NULL,
    audience TEXT NOT NULL,
    focus TEXT NOT NULL,
    context TEXT NOT NULL,
    PRIMARY KEY (case_id, audience)
);

CREATE TABLE engagement_profiles (
    case_id TEXT NOT NULL,
    profile_scope TEXT NOT NULL,
    content TEXT NOT NULL,
    PRIMARY KEY (case_id, profile_scope)
);

CREATE TABLE targets (
    target_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    target_type TEXT NOT NULL,
    title TEXT NOT NULL,
    platform TEXT NOT NULL,
    summary TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE evidence_bundles (
    bundle_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    evidence_scope TEXT NOT NULL,
    summary TEXT NOT NULL,
    notes TEXT NOT NULL
);

CREATE TABLE claim_reviews (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    case_id TEXT NOT NULL,
    claim_scope TEXT NOT NULL,
    bundle_id TEXT NOT NULL,
    verdict TEXT NOT NULL,
    summary TEXT NOT NULL,
    authored_by TEXT NOT NULL
);

CREATE TABLE public_artifacts (
    artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    case_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    claim_scope TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL,
    risk_note TEXT NOT NULL DEFAULT '',
    risk_level TEXT NOT NULL DEFAULT '',
    authored_by TEXT NOT NULL
);

CREATE TABLE risk_reviews (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    case_id TEXT NOT NULL,
    artifact_id INTEGER NOT NULL,
    review_scope TEXT NOT NULL,
    recommendation TEXT NOT NULL,
    redaction_note TEXT NOT NULL,
    authored_by TEXT NOT NULL
);

CREATE TABLE public_actions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    case_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    action_ref TEXT NOT NULL,
    execution_note TEXT NOT NULL,
    status TEXT NOT NULL,
    authored_by TEXT NOT NULL
);

CREATE TABLE stakeholder_threads (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    case_id TEXT NOT NULL,
    stakeholder_id TEXT NOT NULL,
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


def _artifact_seq(value: Any) -> int | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.startswith("draft-"):
        raw = raw.split("-", 1)[1]
    try:
        return int(raw)
    except ValueError:
        return None


class SocialInteractionBank:
    """SQLite-backed stateful backend for public-engagement workflows."""

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
                "INSERT INTO cases (case_id, family, source_benchmark, objective, publication_threshold, anonymity_preference, budget_ceiling, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    case_id,
                    payload["family"],
                    payload["source_benchmark"],
                    payload["objective"],
                    payload["publication_threshold"],
                    payload["anonymity_preference"],
                    float(payload.get("budget_ceiling", 0.0)),
                    payload.get("status", "open"),
                ),
            )

            for audience, packet in payload.get("packets", {}).items():
                self.conn.execute(
                    "INSERT INTO case_packets (case_id, audience, focus, context) VALUES (?, ?, ?, ?)",
                    (case_id, audience, packet["focus"], packet["context"]),
                )

            for profile_scope, content in payload.get("profiles", {}).items():
                self.conn.execute(
                    "INSERT INTO engagement_profiles (case_id, profile_scope, content) VALUES (?, ?, ?)",
                    (case_id, profile_scope, content),
                )

            for target_id, target in payload.get("targets", {}).items():
                self.conn.execute(
                    "INSERT INTO targets (target_id, case_id, target_type, title, platform, summary, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        target_id,
                        case_id,
                        target["target_type"],
                        target["title"],
                        target["platform"],
                        target["summary"],
                        target.get("status", "open"),
                    ),
                )

            for bundle_id, bundle in payload.get("evidence_bundles", {}).items():
                self.conn.execute(
                    "INSERT INTO evidence_bundles (bundle_id, case_id, evidence_scope, summary, notes) VALUES (?, ?, ?, ?, ?)",
                    (
                        bundle_id,
                        case_id,
                        bundle["evidence_scope"],
                        bundle["summary"],
                        bundle["notes"],
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
        return str(case_id or self._active_case_id or "").strip()

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

    def tool_get_engagement_profile(
        self,
        case_id: str = "",
        profile_scope: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_case_id = self._resolve_case_id(case_id)
        if not resolved_case_id:
            return "Engagement profile request is missing case_id."

        case_row = self._case_row(resolved_case_id)
        if case_row is None:
            return f"Case not found: {resolved_case_id}"

        audience_key = str(self._current_agent_role or "public_engagement_lead").strip()
        packet_row = self.conn.execute(
            "SELECT * FROM case_packets WHERE case_id = ? AND audience = ?",
            (resolved_case_id, audience_key),
        ).fetchone()

        rows = self._rows_for_case("engagement_profiles", resolved_case_id, "profile_scope")
        selected = self._filter_rows(rows, profile_scope, fields=("profile_scope", "content"))
        if not selected:
            selected = rows

        lines = [
            f"Engagement Profile - {resolved_case_id}",
            f"Family: {case_row['family']}",
            f"Objective: {case_row['objective']}",
            f"Publication Threshold: {case_row['publication_threshold']}",
            f"Anonymity Preference: {case_row['anonymity_preference']}",
        ]
        if float(case_row["budget_ceiling"]) > 0:
            lines.append(f"Budget Ceiling: ${float(case_row['budget_ceiling']):,.2f}")
        if packet_row is not None:
            lines.extend(
                [
                    f"Audience: {packet_row['audience']}",
                    f"Audience Focus: {packet_row['focus']}",
                    f"Scope Note: {packet_row['context']}",
                ]
            )
        for row in selected:
            lines.append(f"- {row['profile_scope']}: {row['content']}")
        return "\n".join(lines)

    def tool_read_public_target(
        self,
        case_id: str = "",
        target_id: str = "",
        target_scope: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_case_id = self._resolve_case_id(case_id)
        if not resolved_case_id:
            return "Public target request is missing case_id."

        rows = self._rows_for_case("targets", resolved_case_id, "target_type, target_id")
        query = ",".join(part for part in [target_id, target_scope] if str(part).strip())
        selected = self._filter_rows(
            rows,
            query,
            fields=("target_id", "target_type", "title", "platform", "summary"),
        )
        if not selected:
            available = ", ".join(row["target_id"] for row in rows) if rows else "(none)"
            return (
                f"Public Target - {resolved_case_id}\n"
                f"No target matched '{query}'. Available targets: {available}"
            )

        lines = [
            f"Public Target - {resolved_case_id}",
            f"Requested Scope: {target_scope or target_id or 'all'}",
        ]
        for row in selected:
            lines.append(
                f"- {row['target_id']} | {row['target_type']} | {row['title']} "
                f"on {row['platform']} | status={row['status']} | {row['summary']}"
            )
        return "\n".join(lines)

    def tool_collect_evidence_bundle(
        self,
        case_id: str = "",
        evidence_scope: str = "",
        bundle_id: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_case_id = self._resolve_case_id(case_id)
        if not resolved_case_id:
            return "Evidence bundle request is missing case_id."

        rows = self._rows_for_case("evidence_bundles", resolved_case_id, "bundle_id")
        query = ",".join(part for part in [bundle_id, evidence_scope] if str(part).strip())
        selected = self._filter_rows(
            rows,
            query,
            fields=("bundle_id", "evidence_scope", "summary", "notes"),
        )
        if not selected:
            available = ", ".join(row["bundle_id"] for row in rows) if rows else "(none)"
            return (
                f"Evidence Bundle - {resolved_case_id}\n"
                f"No evidence matched '{query}'. Available bundles: {available}"
            )

        lines = [
            f"Evidence Bundle - {resolved_case_id}",
            f"Requested Scope: {evidence_scope or bundle_id or 'all'}",
        ]
        for row in selected:
            lines.append(f"- {row['bundle_id']} [{row['evidence_scope']}]: {row['summary']}")
            lines.append(f"  Notes: {row['notes']}")
        return "\n".join(lines)

    def tool_verify_public_claims(
        self,
        case_id: str = "",
        claim_scope: str = "",
        bundle_id: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_case_id = self._resolve_case_id(case_id)
        if not resolved_case_id:
            return "Claim verification request is missing case_id."

        resolved_bundle_id = str(bundle_id or "").strip()
        if not resolved_bundle_id:
            row = self.conn.execute(
                "SELECT bundle_id FROM evidence_bundles WHERE case_id = ? ORDER BY bundle_id LIMIT 1",
                (resolved_case_id,),
            ).fetchone()
            resolved_bundle_id = str(row["bundle_id"]).strip() if row is not None else ""
        if not resolved_bundle_id:
            return f"No evidence bundle available for {resolved_case_id}."

        verdict = "supported_with_caveats"
        if "rollback" in _norm(claim_scope) or "contradict" in _norm(claim_scope):
            verdict = "partially_supported"
        if "anonymous" in _norm(claim_scope):
            verdict = "supported_after_redaction"

        summary = (
            f"Claim scope '{claim_scope or 'default'}' reviewed against {resolved_bundle_id}; "
            f"verdict={verdict}."
        )
        self.conn.execute(
            "INSERT INTO claim_reviews (timestamp, case_id, claim_scope, bundle_id, verdict, summary, authored_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                _now_iso(),
                resolved_case_id,
                str(claim_scope or "default").strip(),
                resolved_bundle_id,
                verdict,
                summary,
                self._current_agent_role or "unknown",
            ),
        )
        self.conn.commit()
        return "\n".join(
            [
                f"Claim Review - {resolved_case_id}",
                f"Bundle: {resolved_bundle_id}",
                f"Claim Scope: {claim_scope or 'default'}",
                f"Verdict: {verdict}",
                f"Summary: {summary}",
            ]
        )

    def tool_draft_public_artifact(
        self,
        case_id: str = "",
        artifact_type: str = "",
        target_id: str = "",
        claim_scope: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_case_id = self._resolve_case_id(case_id)
        if not resolved_case_id:
            return "Public draft request is missing case_id."
        resolved_target_id = str(target_id or "").strip()
        if not resolved_target_id:
            target_row = self.conn.execute(
                "SELECT target_id FROM targets WHERE case_id = ? ORDER BY target_id LIMIT 1",
                (resolved_case_id,),
            ).fetchone()
            resolved_target_id = str(target_row["target_id"]).strip() if target_row is not None else ""
        if not resolved_target_id:
            return f"No public target available for {resolved_case_id}."

        artifact_kind = str(artifact_type or "public_note").strip()
        prefix = "Draft:"
        if any(token in _norm(artifact_kind) for token in ("review", "forum", "statement", "post")):
            prefix = "ANON-DRAFT:"

        content = (
            f"{prefix} {artifact_kind} for {resolved_target_id}. "
            f"Claim scope: {claim_scope or 'default'}. Keep scope bounded and evidence-backed."
        )
        cursor = self.conn.execute(
            "INSERT INTO public_artifacts (timestamp, case_id, artifact_type, target_id, claim_scope, content, status, authored_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _now_iso(),
                resolved_case_id,
                artifact_kind,
                resolved_target_id,
                str(claim_scope or "default").strip(),
                content,
                "draft",
                self._current_agent_role or "unknown",
            ),
        )
        self.conn.commit()
        artifact_id = int(cursor.lastrowid)
        return "\n".join(
            [
                f"Public Artifact Drafted - {resolved_case_id}",
                f"Artifact ID: draft-{artifact_id}",
                f"Target: {resolved_target_id}",
                f"Type: {artifact_kind}",
                f"Content: {content}",
            ]
        )

    def tool_review_engagement_risk(
        self,
        case_id: str = "",
        artifact_id: str = "",
        review_scope: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_case_id = self._resolve_case_id(case_id)
        if not resolved_case_id:
            return "Engagement risk review request is missing case_id."

        resolved_artifact_id = _artifact_seq(artifact_id)
        if resolved_artifact_id is None:
            return "Engagement risk review requires a valid artifact_id like draft-1."

        artifact_row = self.conn.execute(
            "SELECT * FROM public_artifacts WHERE case_id = ? AND artifact_id = ?",
            (resolved_case_id, resolved_artifact_id),
        ).fetchone()
        if artifact_row is None:
            return f"Artifact not found: draft-{resolved_artifact_id}"

        scope_norm = _norm(review_scope)
        risk_level = "medium"
        if any(token in scope_norm for token in ("identity", "anonym", "privacy", "donation")):
            risk_level = "high"
        recommendation = (
            "redact identity markers and tighten unsupported claims"
            if risk_level == "high"
            else "safe with scoped wording"
        )
        redaction_note = (
            f"Review scope '{review_scope or 'default'}' -> {recommendation}."
        )
        self.conn.execute(
            "INSERT INTO risk_reviews (timestamp, case_id, artifact_id, review_scope, recommendation, redaction_note, authored_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                _now_iso(),
                resolved_case_id,
                resolved_artifact_id,
                str(review_scope or "default").strip(),
                recommendation,
                redaction_note,
                self._current_agent_role or "unknown",
            ),
        )
        self.conn.execute(
            "UPDATE public_artifacts SET risk_note = ?, risk_level = ?, status = ? WHERE artifact_id = ?",
            (redaction_note, risk_level, "reviewed", resolved_artifact_id),
        )
        self.conn.commit()
        return "\n".join(
            [
                f"Engagement Risk Review - {resolved_case_id}",
                f"Artifact: draft-{resolved_artifact_id}",
                f"Risk Level: {risk_level}",
                f"Recommendation: {recommendation}",
                f"Redaction Note: {redaction_note}",
            ]
        )

    def tool_execute_public_action(
        self,
        case_id: str = "",
        action_type: str = "",
        target_id: str = "",
        action_ref: str = "",
        execution_note: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_case_id = self._resolve_case_id(case_id)
        if not resolved_case_id:
            return "Public action execution request is missing case_id."

        resolved_target_id = str(target_id or "").strip()
        if not resolved_target_id:
            target_row = self.conn.execute(
                "SELECT target_id FROM targets WHERE case_id = ? ORDER BY target_id LIMIT 1",
                (resolved_case_id,),
            ).fetchone()
            resolved_target_id = str(target_row["target_id"]).strip() if target_row is not None else ""
        if not resolved_target_id:
            return f"No target available for action on {resolved_case_id}."

        resolved_action_type = str(action_type or "publish").strip()
        new_status = "executed"
        if "hold" in _norm(resolved_action_type):
            new_status = "held"
        elif "donat" in _norm(resolved_action_type):
            new_status = "donated"
        elif "sign" in _norm(resolved_action_type):
            new_status = "signed"
        elif "dispute" in _norm(resolved_action_type):
            new_status = "dispute_opened"

        self.conn.execute(
            "INSERT INTO public_actions (timestamp, case_id, action_type, target_id, action_ref, execution_note, status, authored_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _now_iso(),
                resolved_case_id,
                resolved_action_type,
                resolved_target_id,
                str(action_ref or "").strip(),
                str(execution_note or "").strip(),
                new_status,
                self._current_agent_role or "unknown",
            ),
        )
        self.conn.execute(
            "UPDATE cases SET status = ? WHERE case_id = ?",
            (new_status, resolved_case_id),
        )
        self.conn.commit()
        return (
            f"Executed public action '{resolved_action_type}' for {resolved_case_id} "
            f"against {resolved_target_id}."
        )

    def tool_message_stakeholder(
        self,
        case_id: str = "",
        stakeholder_id: str = "",
        message_type: str = "",
        content: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_case_id = self._resolve_case_id(case_id)
        if not resolved_case_id:
            return "Stakeholder message request is missing case_id."
        if not str(stakeholder_id).strip():
            return "Stakeholder message request is missing stakeholder_id."

        self.conn.execute(
            "INSERT INTO stakeholder_threads (timestamp, case_id, stakeholder_id, message_type, content, authored_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                _now_iso(),
                resolved_case_id,
                str(stakeholder_id).strip(),
                str(message_type or "follow_up").strip(),
                str(content or "").strip(),
                self._current_agent_role or "unknown",
            ),
        )
        self.conn.commit()
        return (
            f"Stakeholder message queued for {stakeholder_id} on {resolved_case_id}."
        )
