"""RealEstateBank — SQLite-backed stateful backend for real-estate tasks."""

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
    deadline TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE case_packets (
    case_id TEXT NOT NULL,
    audience TEXT NOT NULL,
    focus TEXT NOT NULL,
    context TEXT NOT NULL,
    PRIMARY KEY (case_id, audience)
);

CREATE TABLE client_profiles (
    case_id TEXT NOT NULL,
    profile_scope TEXT NOT NULL,
    content TEXT NOT NULL,
    PRIMARY KEY (case_id, profile_scope)
);

CREATE TABLE listings (
    listing_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    listing_type TEXT NOT NULL,
    title TEXT NOT NULL,
    city TEXT NOT NULL,
    price REAL NOT NULL,
    summary TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE property_media (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    listing_id TEXT NOT NULL,
    media_scope TEXT NOT NULL,
    summary TEXT NOT NULL
);

CREATE TABLE property_documents (
    document_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    listing_id TEXT NOT NULL,
    document_type TEXT NOT NULL,
    section_scope TEXT NOT NULL,
    content TEXT NOT NULL
);

CREATE TABLE draft_summaries (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    case_id TEXT NOT NULL,
    summary_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    source_bundle TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL,
    authored_by TEXT NOT NULL
);

CREATE TABLE scheduled_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    case_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    schedule_note TEXT NOT NULL,
    status TEXT NOT NULL,
    authored_by TEXT NOT NULL
);

CREATE TABLE transaction_updates (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    case_id TEXT NOT NULL,
    update_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL,
    authored_by TEXT NOT NULL
);

CREATE TABLE party_threads (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    case_id TEXT NOT NULL,
    party_id TEXT NOT NULL,
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


def _money(value: float) -> str:
    return f"${value:,.0f}"


class RealEstateBank:
    """SQLite-backed stateful backend for real-estate workflows."""

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
                "INSERT INTO cases (case_id, family, source_benchmark, objective, deadline, status) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    case_id,
                    payload["family"],
                    payload["source_benchmark"],
                    payload["objective"],
                    payload["deadline"],
                    payload.get("status", "open"),
                ),
            )

            for audience, packet in payload.get("packets", {}).items():
                self.conn.execute(
                    "INSERT INTO case_packets (case_id, audience, focus, context) VALUES (?, ?, ?, ?)",
                    (case_id, audience, packet["focus"], packet["context"]),
                )

            for profile_scope, content in payload.get("client_profiles", {}).items():
                self.conn.execute(
                    "INSERT INTO client_profiles (case_id, profile_scope, content) VALUES (?, ?, ?)",
                    (case_id, profile_scope, content),
                )

            for listing_id, listing in payload.get("listings", {}).items():
                self.conn.execute(
                    "INSERT INTO listings (listing_id, case_id, listing_type, title, city, price, summary, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        listing_id,
                        case_id,
                        listing["listing_type"],
                        listing["title"],
                        listing["city"],
                        float(listing["price"]),
                        listing["summary"],
                        listing.get("status", "active"),
                    ),
                )

            for media in payload.get("property_media", []):
                self.conn.execute(
                    "INSERT INTO property_media (case_id, listing_id, media_scope, summary) VALUES (?, ?, ?, ?)",
                    (case_id, media["listing_id"], media["media_scope"], media["summary"]),
                )

            for document_id, doc in payload.get("property_documents", {}).items():
                self.conn.execute(
                    "INSERT INTO property_documents (document_id, case_id, listing_id, document_type, section_scope, content) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        document_id,
                        case_id,
                        doc["listing_id"],
                        doc["document_type"],
                        doc["section_scope"],
                        doc["content"],
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

    def tool_get_client_packet(
        self,
        case_id: str = "",
        packet_scope: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_case_id = self._resolve_case_id(case_id)
        if not resolved_case_id:
            return "Client packet request is missing case_id."

        case_row = self._case_row(resolved_case_id)
        if case_row is None:
            return f"Case not found: {resolved_case_id}"

        audience_key = str(self._current_agent_role or "real_estate_lead").strip()
        packet_row = self.conn.execute(
            "SELECT * FROM case_packets WHERE case_id = ? AND audience = ?",
            (resolved_case_id, audience_key),
        ).fetchone()

        rows = self._rows_for_case("client_profiles", resolved_case_id, "profile_scope")
        selected = self._filter_rows(rows, packet_scope, fields=("profile_scope", "content"))
        if not selected:
            selected = rows

        lines = [
            f"Client Packet - {resolved_case_id}",
            f"Family: {case_row['family']}",
            f"Objective: {case_row['objective']}",
            f"Deadline: {case_row['deadline']}",
        ]
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

    def tool_get_listing_packet(
        self,
        case_id: str = "",
        listing_id: str = "",
        packet_scope: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_case_id = self._resolve_case_id(case_id)
        if not resolved_case_id:
            return "Listing packet request is missing case_id."

        rows = self._rows_for_case("listings", resolved_case_id, "price ASC, listing_id")
        query = ",".join(part for part in [listing_id, packet_scope] if str(part).strip())
        selected = self._filter_rows(
            rows,
            query,
            fields=("listing_id", "listing_type", "title", "city", "summary"),
        )
        if not selected:
            available = ", ".join(row["listing_id"] for row in rows) if rows else "(none)"
            return (
                f"Listing Packet - {resolved_case_id}\n"
                f"No listing matched '{query}'. Available listings: {available}"
            )

        lines = [
            f"Listing Packet - {resolved_case_id}",
            f"Requested Scope: {packet_scope or listing_id or 'all'}",
        ]
        for row in selected:
            lines.append(
                f"- {row['listing_id']} | {row['title']} | {row['listing_type']} | "
                f"{row['city']} | price={_money(float(row['price']))} | {row['summary']}"
            )
        return "\n".join(lines)

    def tool_review_property_media(
        self,
        case_id: str = "",
        listing_id: str = "",
        media_scope: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_case_id = self._resolve_case_id(case_id)
        if not resolved_case_id:
            return "Property media request is missing case_id."

        rows = self._rows_for_case("property_media", resolved_case_id, "listing_id, seq")
        query = ",".join(part for part in [listing_id, media_scope] if str(part).strip())
        selected = self._filter_rows(
            rows,
            query,
            fields=("listing_id", "media_scope", "summary"),
        )
        if not selected:
            available = ", ".join(str(row["listing_id"]) for row in rows) if rows else "(none)"
            return (
                f"Property Media - {resolved_case_id}\n"
                f"No media matched '{query}'. Available listing media: {available}"
            )

        lines = [
            f"Property Media - {resolved_case_id}",
            f"Requested Scope: {media_scope or listing_id or 'all'}",
        ]
        for row in selected:
            lines.append(f"- {row['listing_id']} [{row['media_scope']}]: {row['summary']}")
        return "\n".join(lines)

    def tool_review_property_documents(
        self,
        case_id: str = "",
        listing_id: str = "",
        document_id: str = "",
        section_scope: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_case_id = self._resolve_case_id(case_id)
        if not resolved_case_id:
            return "Property document request is missing case_id."

        rows = self._rows_for_case("property_documents", resolved_case_id, "listing_id, document_type, document_id")
        query = ",".join(part for part in [listing_id, document_id, section_scope] if str(part).strip())
        selected = self._filter_rows(
            rows,
            query,
            fields=("listing_id", "document_id", "document_type", "section_scope", "content"),
        )
        if not selected:
            available = ", ".join(row["document_id"] for row in rows) if rows else "(none)"
            return (
                f"Property Document - {resolved_case_id}\n"
                f"No document matched '{query}'. Available docs: {available}"
            )

        lines = [
            f"Property Document - {resolved_case_id}",
            f"Requested Scope: {section_scope or document_id or listing_id or 'all'}",
        ]
        for row in selected:
            lines.append(
                f"- {row['document_id']} [{row['listing_id']} / {row['document_type']} / {row['section_scope']}]: {row['content']}"
            )
        return "\n".join(lines)

    def tool_compare_listing_matches(
        self,
        case_id: str = "",
        listing_ids: str = "",
        comparison_axes: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_case_id = self._resolve_case_id(case_id)
        if not resolved_case_id:
            return "Listing comparison request is missing case_id."

        all_rows = self._rows_for_case("listings", resolved_case_id, "price ASC, listing_id")
        ids = _parse_csv(listing_ids)
        selected = [row for row in all_rows if not ids or row["listing_id"] in ids]
        if not selected:
            return f"No listings available for comparison in {resolved_case_id}."

        lines = [
            f"Listing Comparison - {resolved_case_id}",
            f"Comparison Axes: {comparison_axes or 'price,fit,location'}",
        ]
        for row in selected:
            lines.append(
                f"- {row['listing_id']}: {_money(float(row['price']))} | {row['city']} | {row['summary']}"
            )
        return "\n".join(lines)

    def tool_draft_real_estate_summary(
        self,
        case_id: str = "",
        summary_type: str = "",
        target_id: str = "",
        source_bundle: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_case_id = self._resolve_case_id(case_id)
        if not resolved_case_id:
            return "Real-estate summary request is missing case_id."

        resolved_summary_type = str(summary_type or "recommendation").strip()
        prefix = "Draft:"
        if any(token in _norm(resolved_summary_type) for token in ("redline", "negotiation")):
            prefix = "NEGOTIATION-FLOOR:"
        elif any(token in _norm(resolved_summary_type) for token in ("handover", "defect")):
            prefix = "PROPERTY-DEFECT:"

        content = (
            f"{prefix} {resolved_summary_type} for {target_id or 'case target'} "
            f"using {source_bundle or 'default source bundle'}."
        )
        cursor = self.conn.execute(
            "INSERT INTO draft_summaries (timestamp, case_id, summary_type, target_id, source_bundle, content, status, authored_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _now_iso(),
                resolved_case_id,
                resolved_summary_type,
                str(target_id or "").strip(),
                str(source_bundle or "").strip(),
                content,
                "draft",
                self._current_agent_role or "unknown",
            ),
        )
        self.conn.commit()
        raw_summary_id = cursor.lastrowid
        summary_ref = (
            f"summary-{int(raw_summary_id)}"
            if raw_summary_id is not None
            else "summary-unknown"
        )
        return f"Real-estate summary drafted for {resolved_case_id} as {summary_ref}."

    def tool_schedule_real_estate_event(
        self,
        case_id: str = "",
        event_type: str = "",
        target_id: str = "",
        schedule_note: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_case_id = self._resolve_case_id(case_id)
        if not resolved_case_id:
            return "Scheduling request is missing case_id."

        event_kind = str(event_type or "showing").strip()
        note = str(schedule_note or "").strip()
        if "show" in _norm(event_kind) or "handover" in _norm(event_kind):
            note = f"SHOWING-SCHEDULE: {note or 'schedule to be confirmed'}"

        self.conn.execute(
            "INSERT INTO scheduled_events (timestamp, case_id, event_type, target_id, schedule_note, status, authored_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                _now_iso(),
                resolved_case_id,
                event_kind,
                str(target_id or "").strip(),
                note,
                "scheduled",
                self._current_agent_role or "unknown",
            ),
        )
        self.conn.commit()
        return f"Scheduled {event_kind} for {resolved_case_id}."

    def tool_update_transaction_record(
        self,
        case_id: str = "",
        update_type: str = "",
        target_id: str = "",
        content: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_case_id = self._resolve_case_id(case_id)
        if not resolved_case_id:
            return "Transaction update request is missing case_id."

        update_kind = str(update_type or "status_update").strip()
        self.conn.execute(
            "INSERT INTO transaction_updates (timestamp, case_id, update_type, target_id, content, status, authored_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                _now_iso(),
                resolved_case_id,
                update_kind,
                str(target_id or "").strip(),
                str(content or "").strip(),
                "updated",
                self._current_agent_role or "unknown",
            ),
        )
        self.conn.execute(
            "UPDATE cases SET status = ? WHERE case_id = ?",
            (f"updated:{update_kind}", resolved_case_id),
        )
        self.conn.commit()
        return f"Transaction record updated for {resolved_case_id}."

    def tool_message_real_estate_party(
        self,
        case_id: str = "",
        party_id: str = "",
        message_type: str = "",
        content: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_case_id = self._resolve_case_id(case_id)
        if not resolved_case_id:
            return "Real-estate message request is missing case_id."
        if not str(party_id).strip():
            return "Real-estate message request is missing party_id."

        self.conn.execute(
            "INSERT INTO party_threads (timestamp, case_id, party_id, message_type, content, authored_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                _now_iso(),
                resolved_case_id,
                str(party_id).strip(),
                str(message_type or "follow_up").strip(),
                str(content or "").strip(),
                self._current_agent_role or "unknown",
            ),
        )
        self.conn.commit()
        return f"Real-estate party message queued for {party_id} on {resolved_case_id}."
