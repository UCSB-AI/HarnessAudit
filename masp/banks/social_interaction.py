"""SocialInteractionBank — SQLite-backed stateful backend for social and journalism tasks."""

from __future__ import annotations

import inspect
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from masp.banks.audit import (
    ToolWriteBackfill,
    action_role,
    backfill_write_roles,
    json_args,
    reconcile_audit_log,
)
from masp.banks.common import (
    filter_rows_by_query,
    normalize_query_text as _norm,
)


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

JM_SCHEMA_SQL = """
CREATE TABLE stories (
    story_id TEXT PRIMARY KEY,
    family TEXT NOT NULL,
    source_benchmark TEXT NOT NULL,
    objective TEXT NOT NULL,
    deadline TEXT NOT NULL,
    publication_standard TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE sources (
    source_id TEXT PRIMARY KEY,
    story_id TEXT NOT NULL,
    source_scope TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL
);

CREATE TABLE media_assets (
    asset_id TEXT PRIMARY KEY,
    story_id TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    inspection_scope TEXT NOT NULL,
    summary TEXT NOT NULL,
    notes TEXT NOT NULL
);

CREATE TABLE jm_public_records (
    record_ref TEXT PRIMARY KEY,
    story_id TEXT NOT NULL,
    query_scope TEXT NOT NULL,
    summary TEXT NOT NULL,
    content TEXT NOT NULL
);

CREATE TABLE jm_claim_reviews (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    story_id TEXT NOT NULL,
    claim_scope TEXT NOT NULL,
    source_bundle TEXT NOT NULL,
    verdict TEXT NOT NULL,
    summary TEXT NOT NULL,
    authored_by TEXT NOT NULL
);

CREATE TABLE story_drafts (
    draft_id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    story_id TEXT NOT NULL,
    draft_type TEXT NOT NULL,
    source_bundle TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL,
    risk_note TEXT NOT NULL DEFAULT '',
    risk_level TEXT NOT NULL DEFAULT '',
    authored_by TEXT NOT NULL
);

CREATE TABLE jm_risk_reviews (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    story_id TEXT NOT NULL,
    draft_id INTEGER NOT NULL,
    review_scope TEXT NOT NULL,
    recommendation TEXT NOT NULL,
    authored_by TEXT NOT NULL
);

CREATE TABLE comment_requests (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    story_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    request_scope TEXT NOT NULL,
    content TEXT NOT NULL,
    authored_by TEXT NOT NULL
);

CREATE TABLE publication_actions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    story_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    draft_id TEXT NOT NULL,
    action_note TEXT NOT NULL,
    status TEXT NOT NULL,
    authored_by TEXT NOT NULL
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _draft_seq(value: Any) -> int | None:
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
        self.conn.executescript(JM_SCHEMA_SQL)
        self._current_agent_role: str | None = None
        self._active_case_id: str | None = None
        self._active_story_id: str | None = None
        self._seed(seed_dir)

    def _seed(self, seed_dir: Path) -> None:
        with open(seed_dir / "seed_cases.json") as f:
            seed = json.load(f)

        for case_id, payload in seed["cases"].items():
            if not isinstance(payload, dict) or "family" not in payload:
                continue
            self.conn.execute(
                "INSERT OR IGNORE INTO cases (case_id, family, source_benchmark, objective, publication_threshold, anonymity_preference, budget_ceiling, status) "
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

            for profile_scope, content in payload.get("profiles", {}).items():
                self.conn.execute(
                    "INSERT OR IGNORE INTO engagement_profiles (case_id, profile_scope, content) VALUES (?, ?, ?)",
                    (case_id, profile_scope, content),
                )

            for target_id, target in payload.get("targets", {}).items():
                self.conn.execute(
                    "INSERT OR IGNORE INTO targets (target_id, case_id, target_type, title, platform, summary, status) "
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
                    "INSERT OR IGNORE INTO evidence_bundles (bundle_id, case_id, evidence_scope, summary, notes) VALUES (?, ?, ?, ?, ?)",
                    (
                        bundle_id,
                        case_id,
                        bundle["evidence_scope"],
                        bundle["summary"],
                        bundle["notes"],
                    ),
                )

        self.conn.commit()

        self._seed_journalism(seed_dir)

    def _seed_journalism(self, seed_dir: Path) -> None:
        seed_path = seed_dir / "seed_stories.json"
        if not seed_path.exists():
            return

        with open(seed_path) as f:
            seed = json.load(f)

        for story_id, payload in seed.get("stories", {}).items():
            if not isinstance(payload, dict) or "family" not in payload:
                continue
            self.conn.execute(
                "INSERT OR IGNORE INTO stories (story_id, family, source_benchmark, objective, deadline, publication_standard, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    story_id,
                    payload["family"],
                    payload["source_benchmark"],
                    payload["objective"],
                    payload["deadline"],
                    payload["publication_standard"],
                    payload.get("status", "open"),
                ),
            )

            for source_id, source in payload.get("sources", {}).items():
                self.conn.execute(
                    "INSERT OR IGNORE INTO sources (source_id, story_id, source_scope, title, content) VALUES (?, ?, ?, ?, ?)",
                    (source_id, story_id, source["source_scope"], source["title"], source["content"]),
                )

            for asset_id, asset in payload.get("media_assets", {}).items():
                self.conn.execute(
                    "INSERT OR IGNORE INTO media_assets (asset_id, story_id, asset_type, inspection_scope, summary, notes) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (asset_id, story_id, asset["asset_type"], asset["inspection_scope"], asset["summary"], asset["notes"]),
                )

            for record_ref, record in payload.get("public_records", {}).items():
                self.conn.execute(
                    "INSERT OR IGNORE INTO jm_public_records (record_ref, story_id, query_scope, summary, content) VALUES (?, ?, ?, ?, ?)",
                    (record_ref, story_id, record["query_scope"], record["summary"], record["content"]),
                )

        self.conn.commit()

    def set_current_agent(self, role: str | None) -> None:
        self._current_agent_role = role

    def apply_task_metadata(self, metadata: dict[str, Any]) -> None:
        case_id = str(metadata.get("case_id", "")).strip()
        self._active_case_id = case_id or None
        story_id = str(metadata.get("story_id", "")).strip()
        self._active_story_id = story_id or None

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

    def reconcile_trace_actions(self, actions: list[Any]) -> None:
        """Backfill caller roles from normalized traces for MCP-backed runs."""

        def derive_fields(action: Any) -> dict[str, Any]:
            tool_name = str(getattr(action, "tool_name", "") or "")
            return {
                "tool": tool_name,
                "args_json": json_args(action),
                "agent_role": action_role(action),
            }

        reconcile_audit_log(self.conn, actions, derive_fields)
        backfill_write_roles(
            self.conn,
            actions,
            [
                ToolWriteBackfill("claim_reviews", "authored_by", "verify_public_claims"),
                ToolWriteBackfill("public_artifacts", "authored_by", "draft_public_artifact", "artifact_id"),
                ToolWriteBackfill("risk_reviews", "authored_by", "review_engagement_risk"),
                ToolWriteBackfill("public_actions", "authored_by", "execute_public_action"),
                ToolWriteBackfill("stakeholder_threads", "authored_by", "message_stakeholder"),
                ToolWriteBackfill("jm_claim_reviews", "authored_by", "compare_story_claims"),
                ToolWriteBackfill("story_drafts", "authored_by", "draft_story_update", "draft_id"),
                ToolWriteBackfill("jm_risk_reviews", "authored_by", "review_publication_risk"),
                ToolWriteBackfill("comment_requests", "authored_by", "request_subject_comment"),
                ToolWriteBackfill("publication_actions", "authored_by", "publish_story"),
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
        return filter_rows_by_query(rows, query, fields=fields, token_fallback=True)

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

        rows = self._rows_for_case("engagement_profiles", resolved_case_id, "profile_scope")
        selected = self._filter_rows(rows, profile_scope, fields=("profile_scope",))
        if not selected:
            available = ", ".join(row["profile_scope"] for row in rows) if rows else "(none)"
            return (
                f"Engagement Profile - {resolved_case_id}\n"
                f"No profile matched scope '{profile_scope}'. Available scopes: {available}"
            )

        lines = [
            f"Engagement Profile - {resolved_case_id}",
            f"Family: {case_row['family']}",
            f"Objective: {case_row['objective']}",
            f"Publication Threshold: {case_row['publication_threshold']}",
            f"Anonymity Preference: {case_row['anonymity_preference']}",
        ]
        if float(case_row["budget_ceiling"]) > 0:
            lines.append(f"Budget Ceiling: ${float(case_row['budget_ceiling']):,.2f}")
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
            fields=("bundle_id", "evidence_scope"),
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
        raw_artifact_id = cursor.lastrowid
        artifact_id = int(raw_artifact_id) if raw_artifact_id is not None else None
        artifact_ref = f"draft-{artifact_id}" if artifact_id is not None else "draft-unknown"
        return "\n".join(
            [
                f"Public Artifact Drafted - {resolved_case_id}",
                f"Artifact ID: {artifact_ref}",
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

    # ── Journalism / Report tools ────────────────────────────────────────

    def _resolve_story_id(self, story_id: str = "") -> str:
        return str(story_id or self._active_story_id or "").strip()

    def _story_row(self, story_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM stories WHERE story_id = ?", (story_id,),
        ).fetchone()

    def _rows_for_story(self, table: str, story_id: str, order_by: str) -> list[sqlite3.Row]:
        query = f"SELECT * FROM {table} WHERE story_id = ? ORDER BY {order_by}"
        return list(self.conn.execute(query, (story_id,)).fetchall())

    def tool_verify_source_material(
        self, story_id: str = "", source_id: str = "", source_scope: str = "", **kwargs: Any,
    ) -> str:
        resolved_story_id = self._resolve_story_id(story_id)
        if not resolved_story_id:
            return "Source material request is missing story_id."

        rows = self._rows_for_story("sources", resolved_story_id, "source_scope, source_id")
        query = ",".join(part for part in [source_id, source_scope] if str(part).strip())
        selected = self._filter_rows(rows, query, fields=("source_id", "source_scope"))
        if not selected:
            available = ", ".join(row["source_id"] for row in rows) if rows else "(none)"
            return f"Source Material - {resolved_story_id}\nNo source matched '{query}'. Available sources: {available}"

        lines = [f"Source Material - {resolved_story_id}", f"Requested Scope: {source_scope or source_id or 'all'}"]
        for row in selected:
            lines.append(f"- {row['source_id']} [{row['title']} / {row['source_scope']}]: {row['content']}")
        return "\n".join(lines)

    def tool_inspect_media_evidence(
        self, story_id: str = "", asset_id: str = "", inspection_scope: str = "", **kwargs: Any,
    ) -> str:
        resolved_story_id = self._resolve_story_id(story_id)
        if not resolved_story_id:
            return "Media evidence request is missing story_id."

        rows = self._rows_for_story("media_assets", resolved_story_id, "inspection_scope, asset_id")
        query = ",".join(part for part in [asset_id, inspection_scope] if str(part).strip())
        selected = self._filter_rows(rows, query, fields=("asset_id", "inspection_scope"))
        if not selected:
            available = ", ".join(row["asset_id"] for row in rows) if rows else "(none)"
            return f"Media Evidence - {resolved_story_id}\nNo asset matched '{query}'. Available assets: {available}"

        lines = [f"Media Evidence - {resolved_story_id}", f"Requested Scope: {inspection_scope or asset_id or 'all'}"]
        for row in selected:
            lines.append(f"- {row['asset_id']} [{row['asset_type']} / {row['inspection_scope']}]: {row['summary']}")
            lines.append(f"  Notes: {row['notes']}")
        return "\n".join(lines)

    def tool_query_public_records(
        self, story_id: str = "", query_scope: str = "", record_ref: str = "", **kwargs: Any,
    ) -> str:
        resolved_story_id = self._resolve_story_id(story_id)
        if not resolved_story_id:
            return "Public record request is missing story_id."

        rows = self._rows_for_story("jm_public_records", resolved_story_id, "query_scope, record_ref")
        query = ",".join(part for part in [query_scope, record_ref] if str(part).strip())
        selected = self._filter_rows(rows, query, fields=("record_ref", "query_scope"))
        if not selected:
            available = ", ".join(row["record_ref"] for row in rows) if rows else "(none)"
            return f"Public Records - {resolved_story_id}\nNo record matched '{query}'. Available records: {available}"

        lines = [f"Public Records - {resolved_story_id}", f"Requested Scope: {query_scope or record_ref or 'all'}"]
        for row in selected:
            lines.append(f"- {row['record_ref']} [{row['query_scope']}]: {row['summary']}")
            lines.append(f"  Content: {row['content']}")
        return "\n".join(lines)

    def tool_compare_story_claims(
        self, story_id: str = "", claim_scope: str = "", source_bundle: str = "", **kwargs: Any,
    ) -> str:
        resolved_story_id = self._resolve_story_id(story_id)
        if not resolved_story_id:
            return "Claim comparison request is missing story_id."

        verdict = "supported_with_caveats"
        scope_norm = _norm(claim_scope)
        if "casualty" in scope_norm or "rumor" in scope_norm:
            verdict = "hold_unconfirmed"
        if "viral" in scope_norm or "forensics" in scope_norm:
            verdict = "verified_after_crosscheck"

        summary = f"Claim scope '{claim_scope or 'default'}' reviewed with bundle '{source_bundle or 'default'}'; verdict={verdict}."
        self.conn.execute(
            "INSERT INTO jm_claim_reviews (timestamp, story_id, claim_scope, source_bundle, verdict, summary, authored_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_now_iso(), resolved_story_id, str(claim_scope or "default").strip(), str(source_bundle or "").strip(), verdict, summary, self._current_agent_role or "unknown"),
        )
        self.conn.commit()
        return "\n".join([f"Claim Comparison - {resolved_story_id}", f"Claim Scope: {claim_scope or 'default'}", f"Verdict: {verdict}", f"Summary: {summary}"])

    def tool_draft_story_update(
        self, story_id: str = "", draft_type: str = "", source_bundle: str = "", **kwargs: Any,
    ) -> str:
        resolved_story_id = self._resolve_story_id(story_id)
        if not resolved_story_id:
            return "Story draft request is missing story_id."

        draft_kind = str(draft_type or "story_flash").strip()
        content = f"UNPUBLISHED-DRAFT: {draft_kind} for {resolved_story_id} using {source_bundle or 'default source bundle'}."
        cursor = self.conn.execute(
            "INSERT INTO story_drafts (timestamp, story_id, draft_type, source_bundle, content, status, authored_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_now_iso(), resolved_story_id, draft_kind, str(source_bundle or "").strip(), content, "draft", self._current_agent_role or "unknown"),
        )
        self.conn.commit()
        draft_id = int(cursor.lastrowid) if cursor.lastrowid is not None else 0
        return f"Story draft created as draft-{draft_id} for {resolved_story_id}."

    def tool_review_publication_risk(
        self, story_id: str = "", artifact_id: str = "", review_scope: str = "", **kwargs: Any,
    ) -> str:
        resolved_story_id = self._resolve_story_id(story_id)
        if not resolved_story_id:
            return "Publication risk review request is missing story_id."

        resolved_draft_id = _draft_seq(artifact_id)
        if resolved_draft_id is None:
            return "Publication risk review requires a draft id like draft-1."

        draft_row = self.conn.execute(
            "SELECT * FROM story_drafts WHERE story_id = ? AND draft_id = ?",
            (resolved_story_id, resolved_draft_id),
        ).fetchone()
        if draft_row is None:
            return f"Draft not found: draft-{resolved_draft_id}"

        scope_norm = _norm(review_scope)
        risk_level = "medium"
        if any(token in scope_norm for token in ("source", "casualty", "embargo", "legal")):
            risk_level = "high"
        recommendation = "remove unconfirmed detail and protect source identity" if risk_level == "high" else "safe with attribution tightening"
        self.conn.execute(
            "INSERT INTO jm_risk_reviews (timestamp, story_id, draft_id, review_scope, recommendation, authored_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (_now_iso(), resolved_story_id, resolved_draft_id, str(review_scope or "default").strip(), recommendation, self._current_agent_role or "unknown"),
        )
        self.conn.execute(
            "UPDATE story_drafts SET risk_note = ?, risk_level = ?, status = ? WHERE draft_id = ?",
            (recommendation, risk_level, "reviewed", resolved_draft_id),
        )
        self.conn.commit()
        return f"Publication risk reviewed for draft-{resolved_draft_id} on {resolved_story_id}: {risk_level}."

    def tool_request_subject_comment(
        self, story_id: str = "", subject_id: str = "", request_scope: str = "", content: str = "", **kwargs: Any,
    ) -> str:
        resolved_story_id = self._resolve_story_id(story_id)
        if not resolved_story_id:
            return "Comment request is missing story_id."
        if not str(subject_id).strip():
            return "Comment request is missing subject_id."

        self.conn.execute(
            "INSERT INTO comment_requests (timestamp, story_id, subject_id, request_scope, content, authored_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (_now_iso(), resolved_story_id, str(subject_id).strip(), str(request_scope or "general_comment").strip(), str(content or "").strip(), self._current_agent_role or "unknown"),
        )
        self.conn.commit()
        return f"Comment request queued for {subject_id} on {resolved_story_id}."

    def tool_publish_story(
        self, story_id: str = "", action_type: str = "", draft_id: str = "", action_note: str = "", **kwargs: Any,
    ) -> str:
        resolved_story_id = self._resolve_story_id(story_id)
        if not resolved_story_id:
            return "Publish request is missing story_id."

        action_kind = str(action_type or "publish").strip()
        resolved_draft = str(draft_id or "").strip()
        new_status = "published"
        if "hold" in _norm(action_kind):
            new_status = "held"
        elif "correct" in _norm(action_kind):
            new_status = "corrected"

        self.conn.execute(
            "INSERT INTO publication_actions (timestamp, story_id, action_type, draft_id, action_note, status, authored_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_now_iso(), resolved_story_id, action_kind, resolved_draft, str(action_note or "").strip(), new_status, self._current_agent_role or "unknown"),
        )
        self.conn.execute("UPDATE stories SET status = ? WHERE story_id = ?", (new_status, resolved_story_id))
        self.conn.commit()
        return f"Story action '{action_kind}' recorded for {resolved_story_id}."
