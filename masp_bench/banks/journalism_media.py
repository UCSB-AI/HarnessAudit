"""JournalismMediaBank — SQLite-backed stateful backend for journalism tasks."""

from __future__ import annotations

import inspect
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_SQL = """
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

CREATE TABLE public_records (
    record_ref TEXT PRIMARY KEY,
    story_id TEXT NOT NULL,
    query_scope TEXT NOT NULL,
    summary TEXT NOT NULL,
    content TEXT NOT NULL
);

CREATE TABLE claim_reviews (
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

CREATE TABLE risk_reviews (
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


class JournalismMediaBank:
    """SQLite-backed stateful backend for journalism/media workflows."""

    def __init__(self, seed_dir: Path, db_path: str = ":memory:"):
        self.seed_dir = seed_dir
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA_SQL)
        self._current_agent_role: str | None = None
        self._active_story_id: str | None = None
        self._seed(seed_dir)

    def _seed(self, seed_dir: Path) -> None:
        with open(seed_dir / "seed_stories.json") as f:
            seed = json.load(f)

        for story_id, payload in seed["stories"].items():
            self.conn.execute(
                "INSERT INTO stories (story_id, family, source_benchmark, objective, deadline, publication_standard, status) "
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
                    "INSERT INTO sources (source_id, story_id, source_scope, title, content) VALUES (?, ?, ?, ?, ?)",
                    (
                        source_id,
                        story_id,
                        source["source_scope"],
                        source["title"],
                        source["content"],
                    ),
                )

            for asset_id, asset in payload.get("media_assets", {}).items():
                self.conn.execute(
                    "INSERT INTO media_assets (asset_id, story_id, asset_type, inspection_scope, summary, notes) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        asset_id,
                        story_id,
                        asset["asset_type"],
                        asset["inspection_scope"],
                        asset["summary"],
                        asset["notes"],
                    ),
                )

            for record_ref, record in payload.get("public_records", {}).items():
                self.conn.execute(
                    "INSERT INTO public_records (record_ref, story_id, query_scope, summary, content) VALUES (?, ?, ?, ?, ?)",
                    (
                        record_ref,
                        story_id,
                        record["query_scope"],
                        record["summary"],
                        record["content"],
                    ),
                )

        self.conn.commit()

    def set_current_agent(self, role: str | None) -> None:
        self._current_agent_role = role

    def apply_task_metadata(self, metadata: dict[str, Any]) -> None:
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

    def _resolve_story_id(self, story_id: str = "") -> str:
        return str(story_id or self._active_story_id or "").strip()

    def _story_row(self, story_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM stories WHERE story_id = ?",
            (story_id,),
        ).fetchone()

    def _rows_for_story(self, table: str, story_id: str, order_by: str) -> list[sqlite3.Row]:
        query = f"SELECT * FROM {table} WHERE story_id = ? ORDER BY {order_by}"
        return list(self.conn.execute(query, (story_id,)).fetchall())

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

        # Fuzzy fallback: if no rows matched, try word-level matching
        # This handles cases where agents pass filenames or descriptive names
        # that share individual words with the stored keys/titles/content.
        if not selected and normalized_queries:
            query_words = set()
            for q in normalized_queries:
                for word in q.replace(".", " ").replace("_", " ").replace("-", " ").split():
                    word = word.strip()
                    if len(word) >= 3:  # skip very short tokens
                        query_words.add(word)
            if query_words:
                for row in rows:
                    haystacks = [_norm(row[field]) for field in fields]
                    combined = " ".join(haystacks)
                    if any(word in combined for word in query_words):
                        selected.append(row)

        return selected

    def tool_verify_source_material(
        self,
        story_id: str = "",
        source_id: str = "",
        source_scope: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_story_id = self._resolve_story_id(story_id)
        if not resolved_story_id:
            return "Source material request is missing story_id."

        rows = self._rows_for_story("sources", resolved_story_id, "source_scope, source_id")
        query = ",".join(part for part in [source_id, source_scope] if str(part).strip())
        selected = self._filter_rows(
            rows,
            query,
            fields=("source_id", "source_scope", "title", "content"),
        )
        if not selected:
            available = ", ".join(row["source_id"] for row in rows) if rows else "(none)"
            return (
                f"Source Material - {resolved_story_id}\n"
                f"No source matched '{query}'. Available sources: {available}"
            )

        lines = [
            f"Source Material - {resolved_story_id}",
            f"Requested Scope: {source_scope or source_id or 'all'}",
        ]
        for row in selected:
            lines.append(f"- {row['source_id']} [{row['title']} / {row['source_scope']}]: {row['content']}")
        return "\n".join(lines)

    def tool_inspect_media_evidence(
        self,
        story_id: str = "",
        asset_id: str = "",
        inspection_scope: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_story_id = self._resolve_story_id(story_id)
        if not resolved_story_id:
            return "Media evidence request is missing story_id."

        rows = self._rows_for_story("media_assets", resolved_story_id, "inspection_scope, asset_id")
        query = ",".join(part for part in [asset_id, inspection_scope] if str(part).strip())
        selected = self._filter_rows(
            rows,
            query,
            fields=("asset_id", "asset_type", "inspection_scope", "summary", "notes"),
        )
        if not selected:
            available = ", ".join(row["asset_id"] for row in rows) if rows else "(none)"
            return (
                f"Media Evidence - {resolved_story_id}\n"
                f"No asset matched '{query}'. Available assets: {available}"
            )

        lines = [
            f"Media Evidence - {resolved_story_id}",
            f"Requested Scope: {inspection_scope or asset_id or 'all'}",
        ]
        for row in selected:
            lines.append(f"- {row['asset_id']} [{row['asset_type']} / {row['inspection_scope']}]: {row['summary']}")
            lines.append(f"  Notes: {row['notes']}")
        return "\n".join(lines)

    def tool_query_public_records(
        self,
        story_id: str = "",
        query_scope: str = "",
        record_ref: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_story_id = self._resolve_story_id(story_id)
        if not resolved_story_id:
            return "Public record request is missing story_id."

        rows = self._rows_for_story("public_records", resolved_story_id, "query_scope, record_ref")
        query = ",".join(part for part in [query_scope, record_ref] if str(part).strip())
        selected = self._filter_rows(
            rows,
            query,
            fields=("record_ref", "query_scope", "summary", "content"),
        )
        if not selected:
            available = ", ".join(row["record_ref"] for row in rows) if rows else "(none)"
            return (
                f"Public Records - {resolved_story_id}\n"
                f"No record matched '{query}'. Available records: {available}"
            )

        lines = [
            f"Public Records - {resolved_story_id}",
            f"Requested Scope: {query_scope or record_ref or 'all'}",
        ]
        for row in selected:
            lines.append(f"- {row['record_ref']} [{row['query_scope']}]: {row['summary']}")
            lines.append(f"  Content: {row['content']}")
        return "\n".join(lines)

    def tool_compare_story_claims(
        self,
        story_id: str = "",
        claim_scope: str = "",
        source_bundle: str = "",
        **kwargs: Any,
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

        summary = (
            f"Claim scope '{claim_scope or 'default'}' reviewed with bundle "
            f"'{source_bundle or 'default'}'; verdict={verdict}."
        )
        self.conn.execute(
            "INSERT INTO claim_reviews (timestamp, story_id, claim_scope, source_bundle, verdict, summary, authored_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                _now_iso(),
                resolved_story_id,
                str(claim_scope or "default").strip(),
                str(source_bundle or "").strip(),
                verdict,
                summary,
                self._current_agent_role or "unknown",
            ),
        )
        self.conn.commit()
        return "\n".join(
            [
                f"Claim Comparison - {resolved_story_id}",
                f"Claim Scope: {claim_scope or 'default'}",
                f"Verdict: {verdict}",
                f"Summary: {summary}",
            ]
        )

    def tool_draft_story_update(
        self,
        story_id: str = "",
        draft_type: str = "",
        source_bundle: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_story_id = self._resolve_story_id(story_id)
        if not resolved_story_id:
            return "Story draft request is missing story_id."

        draft_kind = str(draft_type or "story_flash").strip()
        content = (
            f"UNPUBLISHED-DRAFT: {draft_kind} for {resolved_story_id} using "
            f"{source_bundle or 'default source bundle'}."
        )
        cursor = self.conn.execute(
            "INSERT INTO story_drafts (timestamp, story_id, draft_type, source_bundle, content, status, authored_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                _now_iso(),
                resolved_story_id,
                draft_kind,
                str(source_bundle or "").strip(),
                content,
                "draft",
                self._current_agent_role or "unknown",
            ),
        )
        self.conn.commit()
        draft_id = int(cursor.lastrowid) if cursor.lastrowid is not None else 0
        return f"Story draft created as draft-{draft_id} for {resolved_story_id}."

    def tool_review_publication_risk(
        self,
        story_id: str = "",
        artifact_id: str = "",
        review_scope: str = "",
        **kwargs: Any,
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
        recommendation = (
            "remove unconfirmed detail and protect source identity"
            if risk_level == "high"
            else "safe with attribution tightening"
        )
        self.conn.execute(
            "INSERT INTO risk_reviews (timestamp, story_id, draft_id, review_scope, recommendation, authored_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                _now_iso(),
                resolved_story_id,
                resolved_draft_id,
                str(review_scope or "default").strip(),
                recommendation,
                self._current_agent_role or "unknown",
            ),
        )
        self.conn.execute(
            "UPDATE story_drafts SET risk_note = ?, risk_level = ?, status = ? WHERE draft_id = ?",
            (recommendation, risk_level, "reviewed", resolved_draft_id),
        )
        self.conn.commit()
        return (
            f"Publication risk reviewed for draft-{resolved_draft_id} on {resolved_story_id}: "
            f"{risk_level}."
        )

    def tool_request_subject_comment(
        self,
        story_id: str = "",
        subject_id: str = "",
        request_scope: str = "",
        content: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_story_id = self._resolve_story_id(story_id)
        if not resolved_story_id:
            return "Comment request is missing story_id."
        if not str(subject_id).strip():
            return "Comment request is missing subject_id."

        self.conn.execute(
            "INSERT INTO comment_requests (timestamp, story_id, subject_id, request_scope, content, authored_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                _now_iso(),
                resolved_story_id,
                str(subject_id).strip(),
                str(request_scope or "general_comment").strip(),
                str(content or "").strip(),
                self._current_agent_role or "unknown",
            ),
        )
        self.conn.commit()
        return f"Comment request queued for {subject_id} on {resolved_story_id}."

    def tool_publish_story(
        self,
        story_id: str = "",
        action_type: str = "",
        draft_id: str = "",
        action_note: str = "",
        **kwargs: Any,
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
            (
                _now_iso(),
                resolved_story_id,
                action_kind,
                resolved_draft,
                str(action_note or "").strip(),
                new_status,
                self._current_agent_role or "unknown",
            ),
        )
        self.conn.execute(
            "UPDATE stories SET status = ? WHERE story_id = ?",
            (new_status, resolved_story_id),
        )
        self.conn.commit()
        return f"Story action '{action_kind}' recorded for {resolved_story_id}."
