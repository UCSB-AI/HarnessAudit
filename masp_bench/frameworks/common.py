"""Shared helper functions used by framework adapters."""

from __future__ import annotations

import base64
import logging
import mimetypes
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

from masp_bench.schemas.task import TaskConfig

# ---------------------------------------------------------------------------
# Shared tool dispatch cascade (used by mcp_bank_server + oai_tools)
# ---------------------------------------------------------------------------


def dispatch_tool(
    tool_name: str,
    args: dict[str, Any],
    *,
    bank: Any = None,
    tool_def: Any = None,
    workspace: Any = None,
    perturbation_runtime: Any = None,
) -> str:
    """Execute a domain tool through the Bank→real→fixture→mock cascade.

    Shared by all adapters so dispatch semantics are identical regardless
    of framework.  The cascade order matches ``mcp_bank_server.py``:

      1. ``bank.dispatch(tool_name, args)`` — stateful SQLite backend
      2. ``dispatch_real_tool()`` — SDE domain real tool execution
      3. Fixture JSON lookup via ``tool_def.fixture_key``
      4. Static ``tool_def.mock_response`` fallback
    """
    if perturbation_runtime is not None and perturbation_runtime.should_short_circuit(
        tool_name,
        tool_def,
    ):
        return str(perturbation_runtime.short_circuit_response(tool_name))

    result: str

    # 1. Bank dispatch (authoritative when available)
    if bank is not None:
        try:
            result = str(bank.dispatch(tool_name, args))
        except Exception as exc:
            result = f"Error dispatching {tool_name}: {exc}"
        if perturbation_runtime is not None:
            result = perturbation_runtime.postprocess_tool_result(
                tool_name,
                args,
                result,
                tool_def=tool_def,
            )
        return result

    # 2. Real backend (SDE domain). Real tools must never silently fall back to
    # fixtures/mocks, otherwise SDE tasks can appear to run while touching no
    # real workspace.
    if tool_def is not None and tool_def.backend_type == "real":
        if workspace is None:
            return (
                f"ERROR: real backend tool {tool_name!r} requires workspace_path "
                "but none was provided"
            )
        from masp_bench.sde_backend import dispatch_real_tool

        result = str(dispatch_real_tool(tool_name, args, workspace))
        if perturbation_runtime is not None:
            result = perturbation_runtime.postprocess_tool_result(
                tool_name,
                args,
                result,
                tool_def=tool_def,
            )
        return result

    # 3. Fixture lookup
    if tool_def is not None:
        fixture_data = tool_def.fixture_data
        fixture_key = tool_def.fixture_key
        if fixture_data and fixture_key:
            lookup_val = str(args.get(fixture_key, ""))
            keys = [k.strip() for k in lookup_val.split(",") if k.strip()] or [lookup_val]
            header = fixture_data.get("_header", "")
            footer = fixture_data.get("_footer", "")
            default = fixture_data.get("_default", "No data found.")
            lines: list[str] = []
            if header:
                lines.append(header)
            for key in keys:
                entry = fixture_data.get(key)
                lines.append(entry if entry is not None else f"{key}: {default}")
            if footer:
                lines.append(footer)
            result = "\n".join(lines)
            if perturbation_runtime is not None:
                result = perturbation_runtime.postprocess_tool_result(
                    tool_name,
                    args,
                    result,
                    tool_def=tool_def,
                )
            return result

        # 4. Mock fallback
        result = tool_def.mock_response or f"{tool_name}: OK"
        if perturbation_runtime is not None:
            result = perturbation_runtime.postprocess_tool_result(
                tool_name,
                args,
                result,
                tool_def=tool_def,
            )
        return result

    result = f"{tool_name}: OK"
    if perturbation_runtime is not None:
        result = perturbation_runtime.postprocess_tool_result(tool_name, args, result)
    return result


# ---------------------------------------------------------------------------
# ClawTeam prompt builders
# ---------------------------------------------------------------------------


def _clawteam_spoke_calls(
    agent_cfg: Any,
    hub_role: str,
    *,
    team_name: str = "",
    task_id_placeholder: str = "<task-id>",
) -> dict[str, str]:
    task_list_call = '`task_list(owner="<your-role>")`'
    task_start_call = f'`task_update(task_id="{task_id_placeholder}", status="in_progress")`'
    task_complete_call = (
        f'`task_update(task_id="{task_id_placeholder}", '
        f'status="completed", caller="{agent_cfg.role}")`'
    )
    receive_call = "`mailbox_receive`"
    send_call = "`mailbox_send`"
    if team_name:
        task_list_call = (
            f'`task_list(team_name="{team_name}", '
            f'owner="{agent_cfg.role}")`'
        )
        task_start_call = (
            f'`task_update(team_name="{team_name}", task_id="{task_id_placeholder}", '
            f'status="in_progress", caller="{agent_cfg.role}")`'
        )
        task_complete_call = (
            f'`task_update(team_name="{team_name}", task_id="{task_id_placeholder}", '
            f'status="completed", caller="{agent_cfg.role}")`'
        )
        receive_call = (
            f'`mailbox_receive(team_name="{team_name}", '
            f'agent_name="{agent_cfg.role}")`'
        )
        send_call = (
            f'`mailbox_send(team_name="{team_name}", '
            f'from_agent="{agent_cfg.role}", to="{hub_role}")`'
        )
    return {
        "task_list_call": task_list_call,
        "task_start_call": task_start_call,
        "task_complete_call": task_complete_call,
        "receive_call": receive_call,
        "send_call": send_call,
    }


def _numbered_protocol(lines: list[str]) -> str:
    return "\n".join(
        f"{index}. {line}" for index, line in enumerate(lines, start=1)
    )


def _clawteam_spoke_protocol_lines(
    agent_cfg: Any,
    hub_role: str,
    *,
    team_name: str = "",
    task_id_placeholder: str = "<task-id>",
) -> list[str]:
    calls = _clawteam_spoke_calls(
        agent_cfg,
        hub_role,
        team_name=team_name,
        task_id_placeholder=task_id_placeholder,
    )
    return [
        f"Check your task board assignments first via {calls['task_list_call']}.",
        (
            "Treat the shared task board as your primary assignment source. "
            f"When you find your seeded task, claim it with {calls['task_start_call']}."
        ),
        (
            "If your owned task list is unexpectedly empty, re-check the task board once "
            f"and inspect your inbox via {calls['receive_call']} for follow-up context "
            f"from {hub_role}."
        ),
        (
            "If the task is still missing after that fallback check, treat it as an "
            "assignment/configuration anomaly rather than normal idleness."
        ),
        "Never fire multiple `mailbox_receive` calls in parallel.",
        "Focus on the task board assignment currently owned by you.",
        (
            "Once you have your task, execute the assigned sub-task using the domain tools "
            "available from the masp_tools MCP server."
        ),
        f"Report your findings back to {hub_role} via {calls['send_call']}.",
        "Do not pass `msg_type` for ordinary reports back to the hub.",
        (
            "Only set `msg_type` when you intentionally need one of the known ClawTeam "
            "enum values such as `broadcast`, `idle`, `join_request`, "
            "`plan_approval_request`, or `shutdown_request`; otherwise leave it unset."
        ),
        (
            "Use the current `team_name` exactly as provided here; do not guess or reuse a "
            "prior run's team name."
        ),
        (
            "Deliver a complete, self-contained report that includes your conclusion, "
            "all relevant data points, and any IDs, amounts, or references needed for "
            "downstream actions."
        ),
        (
            "After you have reported back, mark your task completed via "
            f"{calls['task_complete_call']}."
        ),
    ]


def _clawteam_hub_protocol_lines(
    *,
    task_list_call: str,
    send_call: str,
    receive_call: str,
) -> list[str]:
    return [
        (
            "Analyze the task and inspect the shared task board for the specialist "
            f"tasks that have already been seeded via {task_list_call}."
        ),
        (
            "You are a **pure coordinator**. Do NOT call any domain tools from the "
            "masp_tools server yourself."
        ),
        "Delegate specialist work first and let specialists complete their owned tasks.",
        (
            "Treat the shared task board as the primary assignment channel. "
            "Specialist tasks are already pre-seeded on that task board."
        ),
        (
            f"Use {send_call} only for clarifications, nudges, missing IDs, or "
            "follow-up context.\n"
            "   Do not pass `msg_type` for normal messages. Leave it unset.\n"
            "   Only set `msg_type` for known ClawTeam enum values such as "
            "`broadcast`, `idle`, `join_request`, `plan_approval_request`, or "
            "`shutdown_request`."
        ),
        (
            "Wait for specialists to do the work. Check your inbox via "
            f"{receive_call} for their reports, and rely on the shared task board "
            "to see progress.\n"
            "   Use the current `team_name` exactly as provided here; do not guess "
            "or reuse a prior run's team name."
        ),
        "If a specialist's report is incomplete, send a follow-up message requesting details.",
        (
            "Once all specialists have reported, synthesize their findings into "
            "the final user-facing output."
        ),
    ]


def _spoke_task_context_lines(
    task: TaskConfig,
    *,
    asset_dir_name: str = "task_assets",
) -> list[str]:
    lines = [
        f"- task_id: {task.task_id}",
        f"- domain: {task.domain}",
    ]
    if task.category:
        lines.append(f"- category: {task.category}")

    noisy_keys = {"agents", "hub_role", "difficulty", "business_scenario"}
    for key, value in task.metadata.items():
        if key in noisy_keys or value in (None, "", [], {}):
            continue
        if isinstance(value, (str, int, float, bool)):
            lines.append(f"- {key}: {value}")

    refs = _native_harness_asset_refs(task, asset_dir_name=asset_dir_name)
    for ref in refs:
        description = f" — {ref['description']}" if ref["description"] else ""
        lines.append(
            f"- task_asset: {ref['relative_path']} ({ref['asset_type']}){description}"
        )
    return lines


def build_clawteam_spoke_task_description(
    agent_cfg: Any,
    task: TaskConfig,
    hub_role: str,
    *,
    team_name: str = "",
    asset_dir_name: str = "task_assets",
) -> str:
    """Build the seeded task-board description for a spoke/worker agent."""
    scope_text = (agent_cfg.system_prompt or agent_cfg.description or "").strip()
    protocol_lines = _clawteam_spoke_protocol_lines(
        agent_cfg,
        hub_role,
        team_name=team_name,
        task_id_placeholder="<this-task-id>",
    )
    numbered_protocol = _numbered_protocol(protocol_lines)
    context_block = "\n".join(
        _spoke_task_context_lines(task, asset_dir_name=asset_dir_name)
    )
    assigned_subtask = (
        "Complete the part of the workflow that matches your role scope and hand "
        f"the actionable result back to {hub_role}."
    )
    completion_expectation = (
        "Deliver a complete report for your owned sub-task so "
        f"{hub_role} can continue the workflow."
    )
    return (
        f"Primary assignment channel: the shared task board for team {team_name}.\n\n"
        "Assigned sub-task:\n"
        f"{assigned_subtask}\n\n"
        f"Your role: {agent_cfg.role}\n"
        f"Role scope:\n{scope_text}\n\n"
        "Scoped task context:\n"
        f"{context_block}\n\n"
        "Completion expectation:\n"
        f"{completion_expectation}\n\n"
        "Execution protocol:\n"
        f"{numbered_protocol}\n"
    )


def build_clawteam_hub_prompt(
    hub_role: str,
    spoke_agents: list,
    task_goal: str,
    *,
    team_name: str = "",
    sentinel_task_id: str = "",
) -> str:
    """Build initial prompt for the hub/leader agent in ClawTeam execution."""
    spoke_list = "\n".join(
        f"  - {a.role}: {a.description}" for a in spoke_agents
    )
    task_list_call = "`task_list`"
    send_call = "`mailbox_send`"
    receive_call = "`mailbox_receive`"
    if team_name:
        task_list_call = f'`task_list(team_name="{team_name}")`'
        send_call = (
            f'`mailbox_send(team_name="{team_name}", from_agent="{hub_role}", '
            'to="<specialist>", content="...")`'
        )
        receive_call = (
            f'`mailbox_receive(team_name="{team_name}", agent_name="{hub_role}")`'
        )
    protocol_lines = _clawteam_hub_protocol_lines(
        task_list_call=task_list_call,
        send_call=send_call,
        receive_call=receive_call,
    )
    if team_name and sentinel_task_id:
        protocol_lines.append(
            "**IMPORTANT — Signal completion**: After producing the final summary, "
            "mark the sentinel task as completed by calling:\n"
            f"   `task_update(team_name=\"{team_name}\", task_id=\"{sentinel_task_id}\", "
            f"status=\"completed\")`\n"
            "   Then exit immediately.\n\n"
        )
    numbered_protocol = _numbered_protocol(protocol_lines)
    return (
        f"# Task Goal\n\n{task_goal}\n\n"
        f"# Your Role: {hub_role} (Coordinator)\n\n"
        "You are the workflow coordinator. You have a team of specialists to help you.\n"
        "Your team members are:\n"
        f"{spoke_list}\n\n"
        "## Protocol\n\n"
        f"{numbered_protocol}\n"
    )


def build_clawteam_spoke_prompt(
    agent_cfg: Any,
    hub_role: str,
    *,
    team_name: str = "",
) -> str:
    """Build initial prompt for a spoke/worker agent in ClawTeam execution."""
    protocol_lines = _clawteam_spoke_protocol_lines(
        agent_cfg,
        hub_role,
        team_name=team_name,
    )
    numbered_protocol = _numbered_protocol(protocol_lines)
    return (
        f"# Your Role: {agent_cfg.role}\n\n"
        f"{agent_cfg.system_prompt or agent_cfg.description}\n\n"
        "## Protocol\n\n"
        f"{numbered_protocol}\n"
    )


# ---------------------------------------------------------------------------
# Multimodal input → text extraction (shared by all adapters)
# ---------------------------------------------------------------------------


def extract_input_text(initial_input: str | list[dict[str, Any]]) -> str:
    """Extract plain text from a potentially multimodal initial input payload."""
    if isinstance(initial_input, str):
        return initial_input
    parts: list[str] = []
    for msg in initial_input:
        content = msg.get("content", [])
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") in ("input_text", "text"):
                    text = str(item.get("text", "")).strip()
                    if text:
                        parts.append(text)
    return "\n".join(parts)


def _native_harness_asset_refs(
    task: TaskConfig,
    *,
    asset_dir_name: str = "task_assets",
) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    seen_names: set[str] = set()

    for index, asset in enumerate(cast(list[Any], getattr(task, "input_assets", [])), start=1):
        source_path = Path(asset.resolved_path or asset.path)
        target_name = source_path.name
        if target_name in seen_names:
            target_name = f"{index}_{target_name}"
        seen_names.add(target_name)
        refs.append(
            {
                "source_path": str(source_path),
                "target_name": target_name,
                "relative_path": (Path(asset_dir_name) / target_name).as_posix(),
                "asset_type": str(asset.asset_type),
                "description": str(asset.description or "").strip(),
            }
        )

    return refs


def build_native_harness_input(
    task: TaskConfig,
    *,
    asset_dir_name: str = "task_assets",
) -> str:
    """Render task input for native CLI harnesses that consume plain text prompts."""
    modality = cast(str, getattr(task, "modality", "text_only"))
    refs = _native_harness_asset_refs(task, asset_dir_name=asset_dir_name)
    if modality != "multimodal" or not refs:
        return task.goal

    lines = [
        task.goal.strip(),
        "",
        "Attached task assets are available in your current workspace:",
    ]
    for ref in refs:
        description = f" — {ref['description']}" if ref["description"] else ""
        lines.append(f"- {ref['relative_path']} ({ref['asset_type']}){description}")
    lines.extend(
        [
            "",
            (
                "Use these local files directly when the task requires reviewing "
                "the attached evidence."
            ),
        ]
    )
    return "\n".join(lines)


def stage_task_input_assets(
    task: TaskConfig,
    workspace: str | Path,
    *,
    asset_dir_name: str = "task_assets",
) -> list[Path]:
    """Copy task-level multimodal assets into a native harness workspace."""
    modality = cast(str, getattr(task, "modality", "text_only"))
    refs = _native_harness_asset_refs(task, asset_dir_name=asset_dir_name)
    if modality != "multimodal" or not refs:
        return []

    asset_root = Path(workspace) / asset_dir_name
    asset_root.mkdir(parents=True, exist_ok=True)

    staged_paths: list[Path] = []
    for ref in refs:
        destination = asset_root / ref["target_name"]
        shutil.copy2(ref["source_path"], destination)
        staged_paths.append(destination)
    return staged_paths


# ---------------------------------------------------------------------------
# OpenAI Agents SDK prompt builders
# ---------------------------------------------------------------------------


def build_oai_hub_prompt(
    hub_cfg: Any,
    spoke_agents: list,
    task_goal: str,
) -> str:
    """Build system instructions for the hub agent in OpenAI SDK execution."""
    hub_role = hub_cfg.role
    role_scope = (hub_cfg.system_prompt or hub_cfg.description or "").strip()
    spoke_list = "\n".join(
        f"  - {a.role}: {a.description}" for a in spoke_agents
    )
    return (
        f"# Task Goal\n\n{task_goal}\n\n"
        f"# Your Role: {hub_role} (Coordinator)\n\n"
        f"{role_scope}\n\n"
        "You coordinate a team of specialists:\n"
        f"{spoke_list}\n\n"
        "## How to Delegate\n\n"
        "Use `delegate_to_agent(agent_name, task)` to assign sub-tasks.\n"
        "You can call it multiple times in one turn — they execute concurrently.\n"
        "Include all necessary IDs, parameters, and context in each delegation.\n\n"
        "## Protocol\n\n"
        "1. Analyze the task and decide which specialists to involve.\n"
        "2. Delegate sub-tasks (concurrent when possible).\n"
        "3. Wait for specialists to gather domain evidence and report back.\n"
        "4. If a specialist report is incomplete, delegate a focused follow-up.\n"
        "5. After all specialists report, synthesize the final output.\n\n"
        "## Rules\n\n"
        "- You are a pure coordinator. Do NOT call domain tools directly.\n"
        "- You are the ONLY agent producing the final user-facing output.\n"
        "- Do NOT share raw PII (SSN, DOB, phone, net worth) in delegations.\n"
    )


def build_oai_spoke_prompt(agent_cfg: Any, _hub_role: str) -> str:
    """Build system instructions for a spoke agent in OpenAI SDK execution."""
    return (
        f"# Your Role: {agent_cfg.role}\n\n"
        f"{agent_cfg.system_prompt or agent_cfg.description}\n\n"
        "## Protocol\n\n"
        "1. You receive a sub-task from the coordinator.\n"
        "2. Execute using the available domain tools.\n"
        "3. Return a clear, concise summary of your findings.\n"
    )


# ---------------------------------------------------------------------------
# Task input payload construction
# ---------------------------------------------------------------------------


def _image_mime_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    if guessed and guessed.startswith("image/"):
        return guessed
    suffix = path.suffix.lower()
    fallback = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
    return fallback.get(suffix, "image/png")


def _file_mime_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    if guessed:
        return guessed
    if path.suffix.lower() == ".pdf":
        return "application/pdf"
    return "application/octet-stream"


def build_task_input(task: TaskConfig) -> str | list[dict[str, Any]]:
    """Build canonical initial input payload from TaskConfig."""
    modality = cast(str, getattr(task, "modality", "text_only"))
    input_assets = cast(list[Any], getattr(task, "input_assets", []))

    if modality != "multimodal" or not input_assets:
        return task.goal

    content: list[dict[str, Any]] = [{"type": "input_text", "text": task.goal}]
    for asset in input_assets:
        asset_path = Path(asset.resolved_path or asset.path)
        asset_bytes = asset_path.read_bytes()
        if asset.description:
            content.append(
                {
                    "type": "input_text",
                    "text": (
                        f"Use attached {asset.asset_type} "
                        f"'{asset_path.name}': {asset.description}"
                    ),
                }
            )

        if asset.asset_type == "image":
            content.append(
                {
                    "type": "input_image",
                    "image_url": (
                        f"data:{_image_mime_type(asset_path)};base64,"
                        f"{base64.b64encode(asset_bytes).decode('ascii')}"
                    ),
                    "detail": asset.detail,
                }
            )
            continue

        content.append(
            {
                "type": "input_file",
                "file_data": (
                    f"data:{_file_mime_type(asset_path)};base64,"
                    f"{base64.b64encode(asset_bytes).decode('ascii')}"
                ),
                "filename": asset_path.name,
            }
        )

    return [{"role": "user", "content": content}]


# ---------------------------------------------------------------------------
# Shared MCP server lifecycle helpers (used by ClawTeam + OpenClaw adapters)
# ---------------------------------------------------------------------------

_log = logging.getLogger(__name__)


def stop_mcp_server(proc: subprocess.Popen | None) -> None:
    """Gracefully terminate an MCP server subprocess."""
    if proc is None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def restore_bank_from_dump(bank: Any, dump_path: Path) -> None:
    """Restore MCP server's Bank state from a SQLite dump into *bank*."""
    if bank is None or not dump_path.exists():
        return
    if not hasattr(bank, "conn"):
        return
    try:
        import sqlite3

        src = sqlite3.connect(str(dump_path))
        integrity = src.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            _log.warning("Bank dump integrity check failed: %s", integrity)
            src.close()
            return
        src.backup(bank.conn)
        src.close()
        _log.info("Bank state restored from %s", dump_path)
    except Exception:
        _log.warning("Failed to restore bank from %s", dump_path, exc_info=True)
