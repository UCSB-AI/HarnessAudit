from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .jsonl_collector import _strip_mcp_prefix

MCP_SERVER_NAME = "masp_tools"


@dataclass(frozen=True)
class OpenClawLocalIngestionResult:
    final_output: str
    error: str | None = None


def ingest_openclaw_local_session(
    session_path: Path,
    *,
    created_after: float,
    agent_role: str,
    catalog_tool_names: set[str],
    action_sink: Any,
    mcp_server_name: str = MCP_SERVER_NAME,
) -> OpenClawLocalIngestionResult:
    if not session_path.exists():
        return OpenClawLocalIngestionResult(
            final_output="",
            error=f"OpenClaw session file not found: {session_path}",
        )

    if created_after > 0.0 and session_path.stat().st_mtime <= created_after:
        return OpenClawLocalIngestionResult(
            final_output="",
            error=f"OpenClaw session is older than this run: {session_path}",
        )

    final_output = ""
    emitted_tool_calls = 0
    pending_tool_calls: dict[str, dict[str, Any]] = {}

    def _record_timestamp(record: dict[str, Any]) -> str | None:
        ts = record.get("timestamp")
        return str(ts) if ts else None

    try:
        with session_path.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue

                record = json.loads(line)
                if record.get("type") != "message":
                    continue

                message = record.get("message", {})
                role = message.get("role", "")
                content = message.get("content", [])
                if not isinstance(content, list):
                    continue

                if role == "assistant":
                    for item in content:
                        if not isinstance(item, dict):
                            continue
                        item_type = item.get("type")
                        if item_type == "toolCall":
                            raw_name = item.get("name", "")
                            tool_name = _strip_mcp_prefix(raw_name, mcp_server_name)
                            if tool_name not in catalog_tool_names:
                                continue
                            tool_args = item.get("arguments", {})
                            if isinstance(tool_args, str):
                                try:
                                    tool_args = json.loads(tool_args)
                                except json.JSONDecodeError:
                                    tool_args = {"_raw": tool_args}
                            pending_tool_calls[item.get("id", "")] = {
                                "tool_name": tool_name,
                                "tool_args": tool_args,
                                "raw_name": raw_name,
                                "timestamp": _record_timestamp(record),
                                "line_no": line_no,
                            }
                        elif item_type == "text":
                            text = item.get("text", "")
                            if text:
                                final_output = text
                elif role == "toolResult":
                    call_id = message.get("toolCallId", "")
                    pending = pending_tool_calls.pop(call_id, None)
                    if pending is None:
                        continue
                    result_parts: list[str] = []
                    for item in content:
                        if isinstance(item, dict) and "text" in item:
                            result_parts.append(str(item["text"]))
                        elif isinstance(item, str):
                            result_parts.append(item)
                    result_text = "\n".join(result_parts)
                    action_sink.emit_tool_call(
                        agent_id=agent_role,
                        agent_role=agent_role,
                        tool_name=pending["tool_name"],
                        tool_args=pending["tool_args"],
                        tool_result=result_text[:500],
                        raw_event={
                            "framework": "openclaw_local",
                            "harness": "openclaw",
                            "raw_tool_name": pending["raw_name"],
                            "capture_mode": "openclaw_local_session_tool_result",
                            "source_file": str(session_path),
                            "source_line": line_no,
                        },
                        timestamp=_record_timestamp(record) or pending["timestamp"],
                    )
                    emitted_tool_calls += 1
    except json.JSONDecodeError as exc:
        return OpenClawLocalIngestionResult(
            final_output=final_output,
            error=f"Failed to parse OpenClaw transcript {session_path}: {exc}",
        )

    for pending in pending_tool_calls.values():
        action_sink.emit_tool_call(
            agent_id=agent_role,
            agent_role=agent_role,
            tool_name=pending["tool_name"],
            tool_args=pending["tool_args"],
            tool_result="(no result recorded)",
            raw_event={
                "framework": "openclaw_local",
                "harness": "openclaw",
                "raw_tool_name": pending["raw_name"],
                "capture_mode": "openclaw_local_session_orphan_tool_use",
                "source_file": str(session_path),
                "source_line": pending["line_no"],
            },
            timestamp=pending["timestamp"],
        )
        emitted_tool_calls += 1

    if not final_output and emitted_tool_calls == 0:
        return OpenClawLocalIngestionResult(
            final_output="",
            error=(
                "OpenClaw session contained no assistant output or tool "
                f"activity: {session_path}"
            ),
        )

    return OpenClawLocalIngestionResult(final_output=final_output)
