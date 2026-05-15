"""LegalComplianceBank — SQLite-backed stateful backend for legal tasks."""

from __future__ import annotations

import inspect
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from multi_agent.banks.audit import (
    ToolWriteBackfill,
    action_role,
    backfill_write_roles,
    json_args,
    reconcile_audit_log,
)
from multi_agent.banks.common import (
    filter_rows_by_query,
    normalize_query_text as _norm,
    parse_csv_values as _parse_csv,
)


SCHEMA_SQL = """
CREATE TABLE matters (
    matter_id TEXT PRIMARY KEY,
    family TEXT NOT NULL,
    source_benchmark TEXT NOT NULL,
    objective TEXT NOT NULL,
    deadline TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE matter_documents (
    document_id TEXT PRIMARY KEY,
    matter_id TEXT NOT NULL,
    document_type TEXT NOT NULL,
    section_scope TEXT NOT NULL,
    content TEXT NOT NULL
);

CREATE TABLE record_documents (
    document_id TEXT PRIMARY KEY,
    matter_id TEXT NOT NULL,
    document_type TEXT NOT NULL,
    section_scope TEXT NOT NULL,
    content TEXT NOT NULL,
    source_key TEXT NOT NULL
);

CREATE TABLE evidence_artifacts (
    artifact_id TEXT PRIMARY KEY,
    matter_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    verification_note TEXT NOT NULL
);

CREATE TABLE record_artifacts (
    artifact_id TEXT PRIMARY KEY,
    matter_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    verification_note TEXT NOT NULL,
    source_key TEXT NOT NULL
);

CREATE TABLE matter_index_entries (
    entry_id TEXT NOT NULL,
    matter_id TEXT NOT NULL,
    entry_type TEXT NOT NULL,
    label TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    related_document_id TEXT NOT NULL,
    related_entity_id TEXT NOT NULL,
    source_key TEXT NOT NULL,
    PRIMARY KEY (entry_id, matter_id)
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


def _compact_text(*parts: Any) -> str:
    return " ".join(str(part).strip() for part in parts if str(part or "").strip())


def _record_id_candidates(record_id: Any) -> list[str]:
    raw = str(record_id or "").strip()
    if not raw:
        return []

    candidates: list[str] = []
    for candidate in (
        raw,
        raw.removesuffix("-document") + "-pdf" if raw.endswith("-document") else "",
        raw.removesuffix("-pdf") + "-document" if raw.endswith("-pdf") else "",
    ):
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _unique_requested_values(*values: Any) -> list[str]:
    requested: list[str] = []
    for value in values:
        for item in _parse_csv(value):
            if item not in requested:
                requested.append(item)
    return requested


def _infer_task_id_from_key(value: str) -> str:
    match = re.search(r"(lc_t\d+)", str(value or ""))
    if not match:
        return ""
    return match.group(1).replace("_", "-")


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
            if not isinstance(payload, dict) or "family" not in payload:
                continue
            self.conn.execute(
                "INSERT OR IGNORE INTO matters (matter_id, family, source_benchmark, objective, deadline, status) "
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

            for document_id, doc in payload.get("documents", {}).items():
                self.conn.execute(
                    "INSERT OR IGNORE INTO matter_documents (document_id, matter_id, document_type, section_scope, content) "
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
                    "INSERT OR IGNORE INTO evidence_artifacts (artifact_id, matter_id, artifact_type, summary, verification_note) "
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
                    "INSERT OR IGNORE INTO exposure_models (matter_id, exposure_type, estimate_low, estimate_high, summary) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        matter_id,
                        exposure_type,
                        float(exposure["estimate_low"]),
                        float(exposure["estimate_high"]),
                        exposure["summary"],
                    ),
                )

        self._seed_records(seed_dir)
        self.conn.commit()

    def _insert_record_document(
        self,
        document_id: str,
        matter_id: str,
        document_type: str,
        section_scope: str,
        content: str,
        source_key: str,
    ) -> None:
        resolved_document_id = str(document_id or "").strip()
        resolved_matter_id = str(matter_id or "").strip()
        resolved_content = str(content or "").strip()
        if not resolved_document_id or not resolved_matter_id or not resolved_content:
            return

        self.conn.execute(
            "INSERT OR IGNORE INTO record_documents (document_id, matter_id, document_type, section_scope, content, source_key) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                resolved_document_id,
                resolved_matter_id,
                str(document_type or "record_document").strip(),
                str(section_scope or "record").strip(),
                resolved_content,
                str(source_key or "records.json").strip(),
            ),
        )

    def _insert_record_artifact(
        self,
        artifact_id: str,
        matter_id: str,
        artifact_type: str,
        summary: str,
        verification_note: str,
        source_key: str,
    ) -> None:
        resolved_artifact_id = str(artifact_id or "").strip()
        resolved_matter_id = str(matter_id or "").strip()
        resolved_summary = str(summary or "").strip()
        if not resolved_artifact_id or not resolved_matter_id or not resolved_summary:
            return

        self.conn.execute(
            "INSERT OR IGNORE INTO record_artifacts (artifact_id, matter_id, artifact_type, summary, verification_note, source_key) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                resolved_artifact_id,
                resolved_matter_id,
                str(artifact_type or "record_artifact").strip(),
                resolved_summary,
                str(verification_note or "Record-backed artifact.").strip(),
                str(source_key or "records.json").strip(),
            ),
        )

    def _insert_index_entry(
        self,
        entry_id: str,
        matter_id: str,
        entry_type: str,
        label: str,
        status: str,
        summary: str,
        related_document_id: str,
        related_entity_id: str,
        source_key: str,
    ) -> None:
        resolved_entry_id = str(entry_id or "").strip()
        resolved_matter_id = str(matter_id or "").strip()
        if not resolved_entry_id or not resolved_matter_id:
            return

        self.conn.execute(
            "INSERT OR IGNORE INTO matter_index_entries "
            "(entry_id, matter_id, entry_type, label, status, summary, related_document_id, related_entity_id, source_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                resolved_entry_id,
                resolved_matter_id,
                str(entry_type or "index_entry").strip(),
                str(label or resolved_entry_id).strip(),
                str(status or "reference").strip(),
                str(summary or "").strip(),
                str(related_document_id or "").strip(),
                str(related_entity_id or "").strip(),
                str(source_key or "records.json").strip(),
            ),
        )

    def _resolve_index_matter_id(self, payload: Any, default: str = "") -> str:
        if isinstance(payload, dict):
            for key in ("matter_id", "active_matter_id", "linked_matter_id", "target_matter_id", "primary_matter_id"):
                value = str(payload.get(key, "")).strip()
                if value:
                    return value
        return str(default or "").strip()

    def _seed_index_entries(self, records: dict[str, Any]) -> None:
        task_matter_map: dict[str, str] = {}
        for key, payload in records.items():
            if not isinstance(payload, dict):
                continue
            task_id = str(payload.get("task_id", "")).strip()
            matter_id = self._resolve_index_matter_id(payload)
            if task_id and matter_id:
                task_matter_map[task_id] = matter_id
            inferred_task_id = _infer_task_id_from_key(key)
            if inferred_task_id and matter_id and inferred_task_id not in task_matter_map:
                task_matter_map[inferred_task_id] = matter_id

        def default_matter_for(source_key: str, payload: Any) -> str:
            resolved = self._resolve_index_matter_id(payload)
            if resolved:
                return resolved
            inferred_task_id = _infer_task_id_from_key(source_key)
            return task_matter_map.get(inferred_task_id, "")

        def seed_container(source_key: str, payload: Any, default_matter: str = "") -> None:
            if not isinstance(payload, dict):
                return

            container_matter = self._resolve_index_matter_id(payload, default_matter)
            if not container_matter and isinstance(payload.get("task_binding"), dict):
                container_matter = self._resolve_index_matter_id(payload.get("task_binding"), default_matter)

            for entity in payload.get("same_name_entities", []):
                if not isinstance(entity, dict):
                    continue
                entity_matter = self._resolve_index_matter_id(entity, container_matter)
                entity_id = str(entity.get("entity_id") or entity.get("matter_id") or entity.get("entity_name") or "").strip()
                self._insert_index_entry(
                    entity_id,
                    entity_matter,
                    "entity_index",
                    str(entity.get("entity_name") or entity_id).strip(),
                    str(entity.get("status") or "reference").strip(),
                    str(entity.get("note") or "").strip(),
                    "",
                    str(entity.get("entity_id") or entity_id).strip(),
                    source_key,
                )

            for adjacent_id in payload.get("adjacent_entity_ids", []):
                self._insert_index_entry(
                    str(adjacent_id).strip(),
                    container_matter,
                    "adjacent_matter",
                    str(adjacent_id).strip(),
                    "adjacent",
                    _compact_text(payload.get("decoy_note"), payload.get("description"), payload.get("routing_note")),
                    "",
                    "",
                    source_key,
                )

            for field_name in ("version_traps", "outdated_versions", "decoy_records"):
                for record in payload.get(field_name, []):
                    if not isinstance(record, dict):
                        continue
                    record_matter = self._resolve_index_matter_id(record, container_matter)
                    record_id = str(
                        record.get("record_id")
                        or record.get("record_key")
                        or record.get("linked_document")
                        or ""
                    ).strip()
                    self._insert_index_entry(
                        record_id,
                        record_matter,
                        "record_index",
                        str(record.get("linked_document") or record.get("record_key") or record_id).strip(),
                        str(record.get("status") or "reference").strip(),
                        str(record.get("note") or record.get("reason") or "").strip(),
                        str(record.get("linked_document") or record.get("record_key") or "").strip(),
                        "",
                        source_key,
                    )

            if isinstance(payload.get("authorized_action_gate"), dict):
                gate = payload["authorized_action_gate"]
                prerequisites = "; ".join(str(item).strip() for item in gate.get("execution_prerequisites", []))
                self._insert_index_entry(
                    f"{source_key}.authorized_action_gate",
                    container_matter,
                    "action_gate",
                    "authorized_action_gate",
                    "active",
                    _compact_text(
                        f"Coordinator Role: {gate.get('coordinator_role')}" if gate.get("coordinator_role") else "",
                        f"Prerequisites: {prerequisites}" if prerequisites else "",
                    ),
                    "",
                    "",
                    source_key,
                )

            if isinstance(payload.get("notice_path_stub"), dict):
                notice_path = payload["notice_path_stub"]
                self._insert_index_entry(
                    f"{source_key}.notice_path_stub",
                    container_matter,
                    "notice_path",
                    "notice_path_stub",
                    str(notice_path.get("draft_status") or "reference").strip(),
                    _compact_text(
                        f"Target: {notice_path.get('preservation_target')}" if notice_path.get("preservation_target") else "",
                        notice_path.get("summary"),
                    ),
                    "",
                    "",
                    source_key,
                )

            for note_key in ("handling_note", "execution_guardrail", "decoy_note", "routing_note", "description"):
                if container_matter and str(payload.get(note_key, "")).strip():
                    self._insert_index_entry(
                        f"{source_key}.{note_key}",
                        container_matter,
                        "workflow_note",
                        note_key,
                        "active",
                        str(payload.get(note_key) or "").strip(),
                        "",
                        "",
                        source_key,
                    )

            for child_key, child in payload.items():
                if child_key in {
                    "same_name_entities",
                    "adjacent_entity_ids",
                    "version_traps",
                    "outdated_versions",
                    "decoy_records",
                    "authorized_action_gate",
                    "notice_path_stub",
                    "handling_note",
                    "execution_guardrail",
                    "decoy_note",
                    "routing_note",
                    "description",
                }:
                    continue
                if not isinstance(child, dict):
                    continue
                if child_key == "document_aliases" and container_matter:
                    for alias_group, alias_items in child.items():
                        self._insert_index_entry(
                            alias_group,
                            container_matter,
                            "document_alias_group",
                            alias_group,
                            "active",
                            ", ".join(str(item).strip() for item in alias_items),
                            "",
                            "",
                            source_key,
                        )
                    continue
                child_matter = self._resolve_index_matter_id(child, container_matter)
                if not child_matter:
                    continue
                if child_key == "task_binding":
                    self._insert_index_entry(
                        f"{source_key}.task_binding",
                        child_matter,
                        "workflow_note",
                        str(child.get("task_id") or "task_binding").strip(),
                        "active",
                        _compact_text(child.get("business_scenario"), child.get("execution_gate_note")),
                        "",
                        "",
                        source_key,
                    )
                    continue
                if any(field in child for field in ("content", "record_type", "version", "effective_date")):
                    self._insert_index_entry(
                        child_key,
                        child_matter,
                        "record_index",
                        child_key,
                        str(child.get("status") or child.get("version") or "reference").strip(),
                        _compact_text(
                            f"Record Type: {child.get('record_type')}" if child.get("record_type") else "",
                            f"Effective Date: {child.get('effective_date')}" if child.get("effective_date") else "",
                            child.get("content"),
                        ),
                        "",
                        "",
                        source_key,
                    )

        for key, payload in records.items():
            if key.startswith("matter_record_") and isinstance(payload, dict):
                matter_id = self._resolve_index_matter_id(payload)
                self._insert_index_entry(
                    key,
                    matter_id,
                    "record_index",
                    str(payload.get("record_title") or key).strip(),
                    str(payload.get("record_status") or "record").strip(),
                    _compact_text(
                        f"Version: {payload.get('active_version')}" if payload.get("active_version") else "",
                        payload.get("record_notes"),
                    ),
                    key,
                    "",
                    key,
                )
                continue

            if key.startswith("artifact_index_") and isinstance(payload, dict):
                matter_id = self._resolve_index_matter_id(payload)
                summary = _compact_text(
                    "Items: " + ", ".join(str(item).strip() for item in payload.get("bundle_items", [])),
                    payload.get("bundle_note"),
                )
                self._insert_index_entry(
                    key,
                    matter_id,
                    "artifact_index",
                    str(payload.get("bundle_name") or key).strip(),
                    "active",
                    summary,
                    "",
                    "",
                    key,
                )
                bundle_alias = str(payload.get("artifact_bundle_id", "")).strip()
                if bundle_alias:
                    self._insert_index_entry(
                        bundle_alias,
                        matter_id,
                        "artifact_index",
                        str(payload.get("bundle_name") or bundle_alias).strip(),
                        "active",
                        summary,
                        "",
                        "",
                        key,
                    )
                continue

            if key == "legal_compliance_entity_index" and isinstance(payload, dict):
                for entity_id, entity in payload.get("vendor_ids", {}).items():
                    if not isinstance(entity, dict):
                        continue
                    matter_id = self._resolve_index_matter_id(entity)
                    self._insert_index_entry(
                        entity_id,
                        matter_id,
                        "entity_index",
                        str(entity.get("vendor_name") or entity_id).strip(),
                        str(entity.get("status") or "reference").strip(),
                        str(entity.get("descriptor") or "").strip(),
                        "",
                        entity_id,
                        f"{key}.vendor_ids",
                    )
                seed_container(key, payload, default_matter_for(key, payload))
                continue

            if key == "legal_compliance_subpoena_packets" and isinstance(payload, list):
                for packet in payload:
                    if not isinstance(packet, dict):
                        continue
                    matter_id = self._resolve_index_matter_id(packet)
                    packet_id = str(packet.get("packet_id") or "").strip()
                    self._insert_index_entry(
                        packet_id,
                        matter_id,
                        "packet_index",
                        packet_id,
                        str(packet.get("packet_status") or "reference").strip(),
                        str(packet.get("description") or "").strip(),
                        "",
                        "",
                        key,
                    )
                continue

            if key == "legal_compliance_entity_aliases" and isinstance(payload, list):
                for alias in payload:
                    if not isinstance(alias, dict):
                        continue
                    matter_id = self._resolve_index_matter_id(alias)
                    alias_id = str(alias.get("alias_id") or "").strip()
                    self._insert_index_entry(
                        alias_id,
                        matter_id,
                        "entity_alias",
                        _compact_text(alias.get("entity_name"), f"Alias: {alias.get('alias_name')}"),
                        "active",
                        str(alias.get("notes") or "").strip(),
                        "",
                        str(alias.get("entity_name") or "").strip(),
                        key,
                    )
                continue

            if key == "legal_compliance_active_matter_map" and isinstance(payload, dict):
                for map_key, map_payload in payload.items():
                    seed_container(f"{key}.{map_key}", map_payload, default_matter_for(map_key, map_payload))
                continue

            if isinstance(payload, dict):
                seed_container(key, payload, default_matter_for(key, payload))

    def _seed_records(self, seed_dir: Path) -> None:
        records_path = seed_dir / "records.json"
        if not records_path.exists():
            return

        with open(records_path) as f:
            records = json.load(f)

        if not isinstance(records, dict):
            return

        for key, payload in records.items():
            if key.startswith("matter_record_") and isinstance(payload, dict):
                self._insert_record_document(
                    key,
                    str(payload.get("matter_id", "")).strip(),
                    "matter_record",
                    str(payload.get("record_status") or payload.get("active_version") or "record").strip(),
                    _compact_text(
                        payload.get("record_title"),
                        f"Version: {payload.get('active_version')}" if payload.get("active_version") else "",
                        payload.get("record_notes"),
                    ),
                    key,
                )
                continue

            if key.startswith("artifact_index_") and isinstance(payload, dict):
                matter_id = str(payload.get("matter_id", "")).strip()
                summary = _compact_text(
                    payload.get("bundle_name"),
                    "Items: " + ", ".join(str(item).strip() for item in payload.get("bundle_items", [])),
                )
                verification_note = str(payload.get("bundle_note") or "Record-backed artifact bundle.").strip()
                self._insert_record_artifact(
                    key,
                    matter_id,
                    "artifact_bundle",
                    summary,
                    verification_note,
                    key,
                )
                bundle_alias = str(payload.get("artifact_bundle_id", "")).strip()
                if bundle_alias and bundle_alias != key:
                    self._insert_record_artifact(
                        bundle_alias,
                        matter_id,
                        "artifact_bundle",
                        summary,
                        verification_note,
                        key,
                    )

        uploads = records.get("legal_compliance_uploads")
        if isinstance(uploads, dict):
            for upload_key, payload in uploads.items():
                if not isinstance(payload, dict):
                    continue

                for document_id, document in payload.get("uploaded_documents", {}).items():
                    if not isinstance(document, dict):
                        continue
                    self._insert_record_document(
                        document_id,
                        str(document.get("linked_matter_id") or payload.get("active_matter_id") or "").strip(),
                        str(document.get("document_type") or "uploaded_document").strip(),
                        str(document.get("file_name") or "uploaded_document").strip(),
                        str(document.get("content") or "").strip(),
                        f"{upload_key}.uploaded_documents",
                    )

                for artifact_id, artifact in payload.get("uploaded_artifacts", {}).items():
                    if not isinstance(artifact, dict):
                        continue
                    self._insert_record_artifact(
                        artifact_id,
                        str(artifact.get("linked_matter_id") or payload.get("active_matter_id") or "").strip(),
                        str(artifact.get("artifact_type") or "uploaded_artifact").strip(),
                        _compact_text(artifact.get("file_name"), artifact.get("summary")),
                        str(artifact.get("verification_note") or "Uploaded artifact requires verification.").strip(),
                        f"{upload_key}.uploaded_artifacts",
                    )

                for upload_id, upload in payload.get("decoy_uploads", {}).items():
                    if not isinstance(upload, dict):
                        continue
                    if "artifact_type" in upload or "summary" in upload:
                        self._insert_record_artifact(
                            upload_id,
                            str(upload.get("linked_matter_id") or "").strip(),
                            str(upload.get("artifact_type") or "uploaded_artifact").strip(),
                            _compact_text(upload.get("file_name"), upload.get("summary")),
                            str(upload.get("verification_note") or "Decoy artifact requires separate verification.").strip(),
                            f"{upload_key}.decoy_uploads",
                        )
                    else:
                        self._insert_record_document(
                            upload_id,
                            str(upload.get("linked_matter_id") or "").strip(),
                            str(upload.get("document_type") or "uploaded_document").strip(),
                            str(upload.get("file_name") or "uploaded_document").strip(),
                            str(upload.get("content") or "").strip(),
                            f"{upload_key}.decoy_uploads",
                        )

        artifact_store = records.get("artifact_store")
        if isinstance(artifact_store, dict):
            for artifact_id, artifact in artifact_store.items():
                if not isinstance(artifact, dict):
                    continue
                self._insert_record_artifact(
                    artifact_id,
                    str(artifact.get("matter_id") or "").strip(),
                    str(artifact.get("artifact_type") or "artifact_store_item").strip(),
                    _compact_text(
                        artifact.get("title"),
                        f"Source: {artifact.get('source_label')}" if artifact.get("source_label") else "",
                        artifact.get("content_ref"),
                    ),
                    _compact_text(
                        f"Source Label: {artifact.get('source_label')}" if artifact.get("source_label") else "",
                        artifact.get("content_ref"),
                    ),
                    "artifact_store",
                )

        self._seed_index_entries(records)

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
                ToolWriteBackfill("legal_analyses", "authored_by", "draft_legal_analysis"),
                ToolWriteBackfill("prepared_actions", "authored_by", "prepare_legal_action"),
                ToolWriteBackfill("executed_actions", "authored_by", "execute_legal_action"),
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

    def _record_document_rows(
        self,
        matter_id: str,
        *,
        document_id: str = "",
    ) -> list[sqlite3.Row]:
        if document_id:
            candidates = _record_id_candidates(document_id)
            placeholders = ", ".join("?" for _ in candidates)
            return list(
                self.conn.execute(
                    f"SELECT * FROM record_documents WHERE matter_id = ? AND document_id IN ({placeholders}) "
                    "ORDER BY document_type, document_id",
                    [matter_id, *candidates],
                ).fetchall()
            )
        return self._rows_for_matter("record_documents", matter_id, "document_type, document_id")

    def _record_artifact_rows(
        self,
        matter_id: str,
        *,
        artifact_id: str = "",
    ) -> list[sqlite3.Row]:
        if artifact_id:
            return list(
                self.conn.execute(
                    "SELECT * FROM record_artifacts WHERE matter_id = ? AND artifact_id = ? "
                    "ORDER BY artifact_type, artifact_id",
                    (matter_id, artifact_id),
                ).fetchall()
            )
        return self._rows_for_matter("record_artifacts", matter_id, "artifact_type, artifact_id")

    def _index_rows_for_matter(self, matter_id: str) -> list[sqlite3.Row]:
        return self._rows_for_matter("matter_index_entries", matter_id, "entry_type, entry_id")

    def _index_entry_types_for_scope(self, scope: str) -> tuple[str, ...] | None:
        normalized_scope = _norm(scope)
        if not normalized_scope:
            return None

        scope_map = {
            "index": (),
            "registry": (),
            "record index": ("record_index", "artifact_index", "document_alias_group"),
            "entity index": ("entity_index", "adjacent_matter"),
            "alias index": ("entity_alias",),
            "packet index": ("packet_index",),
            "action gate": ("action_gate", "notice_path", "workflow_note"),
            "workflow note": ("workflow_note",),
            "artifact index": ("artifact_index",),
        }
        return scope_map.get(normalized_scope)

    def _selected_index_rows(
        self,
        matter_id: str,
        *,
        scope: str = "",
        entity_id: str = "",
        entry_ids: list[str] | None = None,
        related_document_ids: list[str] | None = None,
    ) -> list[sqlite3.Row]:
        rows = self._index_rows_for_matter(matter_id)
        if not rows:
            return []

        scope_entry_types = self._index_entry_types_for_scope(scope)
        candidate_rows = rows
        if scope_entry_types is not None and scope_entry_types:
            candidate_rows = [row for row in rows if row["entry_type"] in scope_entry_types]

        selected: list[sqlite3.Row] = []
        if scope_entry_types is not None:
            selected.extend(candidate_rows)

        resolved_entity_id = str(entity_id or "").strip()
        if resolved_entity_id:
            selected.extend(
                row
                for row in candidate_rows
                if row["entry_id"] == resolved_entity_id or row["related_entity_id"] == resolved_entity_id
            )

        requested_entry_ids: set[str] = set()
        for value in entry_ids or []:
            requested_entry_ids.update(_record_id_candidates(value))
        if requested_entry_ids:
            selected.extend(
                row
                for row in candidate_rows
                if row["entry_id"] in requested_entry_ids or row["related_document_id"] in requested_entry_ids
            )

        requested_document_ids = set(related_document_ids or [])
        if requested_document_ids:
            selected.extend(
                row
                for row in candidate_rows
                if row["related_document_id"] in requested_document_ids
            )

        deduped: list[sqlite3.Row] = []
        seen: set[tuple[str, str]] = set()
        for row in selected:
            row_key = (str(row["entry_id"]), str(row["matter_id"]))
            if row_key in seen:
                continue
            seen.add(row_key)
            deduped.append(row)
        return deduped

    def _format_index_row(self, row: sqlite3.Row) -> str:
        related_bits: list[str] = []
        if row["related_entity_id"] and row["related_entity_id"] != row["entry_id"]:
            related_bits.append(f"entity {row['related_entity_id']}")
        if row["related_document_id"] and row["related_document_id"] != row["entry_id"]:
            related_bits.append(f"linked {row['related_document_id']}")
        related_suffix = f" ({', '.join(related_bits)})" if related_bits else ""
        label = str(row["label"] or row["entry_id"]).strip()
        prefix = f"- {row['entry_id']} [{row['entry_type']} / {row['status']}]{related_suffix}: {label}"
        summary = str(row["summary"] or "").strip()
        return f"{prefix}. {summary}" if summary else prefix

    def _filter_rows(
        self,
        rows: list[sqlite3.Row],
        query: str,
        *,
        fields: tuple[str, ...],
    ) -> list[sqlite3.Row]:
        return filter_rows_by_query(rows, query, fields=fields)

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

        requested_document_ids = _unique_requested_values(document_id, kwargs.get("document_ids"))
        requested_record_ids = _unique_requested_values(kwargs.get("record_id"), kwargs.get("record_ids"))
        requested_entity_id = str(kwargs.get("entity_id") or "").strip()
        requested_packet_id = str(kwargs.get("packet_id") or "").strip()
        requested_alias_id = str(kwargs.get("alias_id") or "").strip()
        requested_scope = str(kwargs.get("index_scope") or section_scope or "").strip()
        is_index_scope = self._index_entry_types_for_scope(requested_scope) is not None

        core_rows = self._rows_for_matter("matter_documents", resolved_matter_id, "document_type, document_id")
        supplemental_rows = self._record_document_rows(resolved_matter_id)

        selected_documents: list[sqlite3.Row] = []
        if requested_document_ids:
            for requested_document in requested_document_ids:
                matches = self._filter_rows(
                    core_rows,
                    requested_document,
                    fields=("document_id", "document_type", "section_scope"),
                )
                if not matches:
                    matches = self._record_document_rows(
                        resolved_matter_id,
                        document_id=requested_document,
                    )
                    if requested_scope and not is_index_scope and matches:
                        scoped_rows = self._filter_rows(
                            matches,
                            requested_scope,
                            fields=("section_scope", "document_type", "content"),
                        )
                        if scoped_rows:
                            matches = scoped_rows
                selected_documents.extend(matches)
        elif not is_index_scope and not (requested_entity_id or requested_record_ids or requested_packet_id or requested_alias_id):
            document_query = requested_scope if requested_scope else ""
            selected_documents = self._filter_rows(
                core_rows,
                document_query,
                fields=("document_id", "document_type", "section_scope"),
            )
            if not selected_documents:
                selected_documents = self._filter_rows(
                    supplemental_rows,
                    document_query,
                    fields=("document_id", "document_type", "section_scope", "content"),
                )

        deduped_documents: list[sqlite3.Row] = []
        seen_document_ids: set[str] = set()
        for row in selected_documents:
            document_key = str(row["document_id"])
            if document_key in seen_document_ids:
                continue
            seen_document_ids.add(document_key)
            deduped_documents.append(row)
        selected_documents = deduped_documents

        index_entry_ids = _unique_requested_values(requested_record_ids, requested_packet_id, requested_alias_id)
        selected_index_rows = self._selected_index_rows(
            resolved_matter_id,
            scope=requested_scope,
            entity_id=requested_entity_id,
            entry_ids=index_entry_ids,
            related_document_ids=requested_document_ids,
        )

        available_document_rows = core_rows + [
            row for row in supplemental_rows if row["document_id"] not in {core["document_id"] for core in core_rows}
        ]
        available_documents = ", ".join(row["document_id"] for row in available_document_rows) if available_document_rows else "(none)"
        available_index_rows = self._index_rows_for_matter(resolved_matter_id)
        available_index_entries = ", ".join(row["entry_id"] for row in available_index_rows) if available_index_rows else "(none)"

        requested_tokens = [
            ", ".join(requested_document_ids) if requested_document_ids else "",
            requested_scope if requested_scope else "",
            requested_entity_id,
            ", ".join(index_entry_ids) if index_entry_ids else "",
        ]
        requested_query = "; ".join(token for token in requested_tokens if token) or "all"

        if not selected_documents and not selected_index_rows:
            return (
                f"Matter Document - {resolved_matter_id}\n"
                f"No document or index entry matched '{requested_query}'. Available documents: {available_documents}\n"
                f"Available index entries: {available_index_entries}"
            )

        lines = [
            f"Matter Document - {resolved_matter_id}",
            f"Requested Scope: {requested_query}",
        ]
        if selected_index_rows:
            lines.append("Index Context:")
            for row in selected_index_rows:
                lines.append(self._format_index_row(row))
        elif requested_entity_id or index_entry_ids or is_index_scope:
            lines.append(f"Index Context: no records-backed match found. Available index entries: {available_index_entries}")

        if selected_documents:
            lines.append("Documents:")
            for row in selected_documents:
                lines.append(
                    f"- {row['document_id']} [{row['document_type']} / {row['section_scope']}]: {row['content']}"
                )
        elif requested_document_ids:
            lines.append(f"Documents: no document matched. Available documents: {available_documents}")

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

        requested_artifact_ids = _unique_requested_values(artifact_id, kwargs.get("artifact_ids"))
        core_rows = self._rows_for_matter("evidence_artifacts", resolved_matter_id, "artifact_type, artifact_id")
        supplemental_rows = self._record_artifact_rows(resolved_matter_id)

        selected_artifacts: list[sqlite3.Row] = []
        if requested_artifact_ids:
            for requested_artifact in requested_artifact_ids:
                matches = self._filter_rows(
                    core_rows,
                    requested_artifact,
                    fields=("artifact_id", "artifact_type", "summary", "verification_note"),
                )
                if not matches:
                    matches = self._record_artifact_rows(
                        resolved_matter_id,
                        artifact_id=requested_artifact,
                    )
                    if verification_goal and matches:
                        scoped_rows = self._filter_rows(
                            matches,
                            verification_goal,
                            fields=("artifact_type", "summary", "verification_note"),
                        )
                        if scoped_rows:
                            matches = scoped_rows
                selected_artifacts.extend(matches)
        else:
            selected_artifacts = self._filter_rows(
                core_rows,
                verification_goal,
                fields=("artifact_id", "artifact_type", "summary", "verification_note"),
            )
            if not selected_artifacts:
                selected_artifacts = self._filter_rows(
                    supplemental_rows,
                    verification_goal,
                    fields=("artifact_id", "artifact_type", "summary", "verification_note"),
                )

        deduped_artifacts: list[sqlite3.Row] = []
        seen_artifact_ids: set[str] = set()
        for row in selected_artifacts:
            artifact_key = str(row["artifact_id"])
            if artifact_key in seen_artifact_ids:
                continue
            seen_artifact_ids.add(artifact_key)
            deduped_artifacts.append(row)

        if not deduped_artifacts:
            available_rows = core_rows + [
                row for row in supplemental_rows if row["artifact_id"] not in {core["artifact_id"] for core in core_rows}
            ]
            available = ", ".join(row["artifact_id"] for row in available_rows) if available_rows else "(none)"
            requested_scope = ", ".join(requested_artifact_ids) if requested_artifact_ids else verification_goal
            return (
                f"Evidence Artifact - {resolved_matter_id}\n"
                f"No artifact matched '{requested_scope or 'default'}'. Available artifacts: {available}"
            )

        lines = [
            f"Evidence Artifact - {resolved_matter_id}",
            f"Verification Goal: {verification_goal or 'default'}",
        ]
        for row in deduped_artifacts:
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

        requested_event_labels = _unique_requested_values(
            timeline_scope,
            kwargs.get("event_labels"),
            kwargs.get("event_ids"),
            kwargs.get("timeline_event_labels"),
            kwargs.get("timeline_event_ids"),
        )
        resolved_timeline_scope = ", ".join(requested_event_labels)
        requested_entity_id = str(kwargs.get("entity_id") or "").strip()
        selected_index_rows = self._selected_index_rows(
            resolved_matter_id,
            entity_id=requested_entity_id,
        )

        rows = self._rows_for_matter("timeline_events", resolved_matter_id, "event_date, seq")
        selected = self._filter_rows(rows, resolved_timeline_scope, fields=("event_label",))
        if not selected:
            available = ", ".join(row["event_label"] for row in rows) if rows else "(none)"
            available_index_entries = ", ".join(
                row["entry_id"] for row in self._index_rows_for_matter(resolved_matter_id)
            ) or "(none)"
            return (
                f"Timeline Comparison - {resolved_matter_id}\n"
                f"No timeline events matched '{resolved_timeline_scope or 'all'}'. Available event labels: {available}\n"
                f"Available index entries: {available_index_entries}"
            )

        lines = [
            f"Timeline Comparison - {resolved_matter_id}",
            f"Timeline Scope: {resolved_timeline_scope or 'all'}",
            f"Comparison Goal: {comparison_goal or 'sequence review'}",
        ]
        if requested_entity_id:
            if selected_index_rows:
                lines.append("Index Context:")
                for row in selected_index_rows:
                    lines.append(self._format_index_row(row))
            else:
                lines.append(f"Index Context: no records-backed match found for entity_id '{requested_entity_id}'.")
        for row in selected:
            lines.append(f"- {row['event_date']} | {row['event_label']}: {row['details']}")
        return "\n".join(lines)

    def tool_compute_exposure_estimate(
        self,
        matter_id: str = "",
        exposure_type: str = "",
        **kwargs: Any,
    ) -> str:
        resolved_matter_id = self._resolve_matter_id(matter_id)
        if not resolved_matter_id:
            return "Exposure estimate request is missing matter_id."

        requested_entity_id = str(kwargs.get("entity_id") or "").strip()
        selected_index_rows = self._selected_index_rows(
            resolved_matter_id,
            entity_id=requested_entity_id,
        )

        resolved_exposure_type = str(exposure_type or kwargs.get("model_id") or "").strip()
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
            # Fuzzy fallback: try substring matching against available exposure types
            all_types = self.conn.execute(
                "SELECT * FROM exposure_models WHERE matter_id = ?",
                (resolved_matter_id,),
            ).fetchall()
            needle = _norm(resolved_exposure_type)
            for candidate in all_types:
                haystack = _norm(candidate["exposure_type"])
                if needle in haystack or haystack in needle:
                    row = candidate
                    resolved_exposure_type = candidate["exposure_type"]
                    break
        if row is None:
            available = ", ".join(
                r["exposure_type"]
                for r in self.conn.execute(
                    "SELECT exposure_type FROM exposure_models WHERE matter_id = ?",
                    (resolved_matter_id,),
                ).fetchall()
            )
            return f"Exposure model not found: {resolved_exposure_type}. Available types: {available or '(none)'}"

        return "\n".join(
            [
                f"Exposure Estimate - {resolved_matter_id}",
                *(
                    ["Index Context:"] + [self._format_index_row(index_row) for index_row in selected_index_rows]
                    if selected_index_rows else
                    ([f"Index Context: no records-backed match found for entity_id '{requested_entity_id}'."] if requested_entity_id else [])
                ),
                f"Exposure Type: {resolved_exposure_type}",
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
