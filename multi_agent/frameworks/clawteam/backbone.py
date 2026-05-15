"""Resolve observed harness LLM backbones from native artifacts.

ClawTeam run IDs are created before native harness logs exist, so they may
initially embed a placeholder/requested model string. After a run completes we
can inspect the native artifacts and canonicalize the trace/result filenames and
metadata with the actual observed backbone (for example ``gpt-5.4`` or
``claude-sonnet-4-6``).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

from ..native.codex.ingestor import _find_codex_sessions
from ..native.openclaw.ingestor import _OPENCLAW_SESSIONS_DIR, openclaw_session_path
from .profiles import session_name


_RUN_ID_RE = re.compile(r"^(?P<framework>[a-z0-9]+)-(?P<harness>[a-z0-9]+)-(?P<model>.+)-(?P<suffix>[0-9a-f]{8})$")


def replace_run_id_model(run_id: str, observed_model: str) -> str:
    """Replace the model component inside a run id while preserving its suffix."""
    match = _RUN_ID_RE.match(run_id)
    if not match:
        return run_id
    framework = match.group("framework")
    harness = match.group("harness")
    suffix = match.group("suffix")
    return f"{framework}-{harness}-{observed_model}-{suffix}"


def _clawteam_worktree(run_id: str, agent_role: str) -> Path:
    from clawteam.team.models import get_data_dir

    return get_data_dir() / "workspaces" / f"multi-agent-{run_id}" / agent_role


def _detect_codex_model(run_id: str, agent_roles: Iterable[str]) -> str | None:
    for role in agent_roles:
        session_files = _find_codex_sessions(_clawteam_worktree(run_id, role), role, created_after=0.0)
        for session_path in session_files:
            with open(session_path) as handle:
                for line in handle:
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if record.get("type") == "turn_context":
                        model = record.get("payload", {}).get("model")
                        if isinstance(model, str) and model:
                            return model
                    if record.get("type") == "session_meta":
                        model = record.get("payload", {}).get("model")
                        if isinstance(model, str) and model:
                            return model
    return None


def _detect_claude_model(run_id: str, agent_roles: Iterable[str]) -> str | None:
    del run_id, agent_roles
    # Claude runs use per-run CLAUDE_CONFIG_DIR isolation. By the time CLI
    # artifact canonicalization runs, the isolated native logs may already be
    # cleaned up, so probing the global ~/.claude/projects fallback is wrong.
    # Keep the requested benchmark model label instead.
    return None


def _detect_openclaw_model(run_id: str, agent_roles: Iterable[str]) -> str | None:
    for role in agent_roles:
        session_path = openclaw_session_path(
            session_name(run_id, role, "openclaw"),
            sessions_dir=_OPENCLAW_SESSIONS_DIR,
        )
        if not session_path.exists():
            continue
        with open(session_path) as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("type") == "model_change":
                    model_id = record.get("modelId")
                    if isinstance(model_id, str) and model_id:
                        return model_id
                if record.get("type") == "message":
                    model = record.get("message", {}).get("model")
                    if isinstance(model, str) and model:
                        return model
    return None


def detect_clawteam_backbone(harness: str, run_id: str, agent_roles: Iterable[str]) -> str | None:
    """Inspect native artifacts to determine the actual model used by a harness."""
    if harness == "codex":
        return _detect_codex_model(run_id, agent_roles)
    if harness == "claude":
        return _detect_claude_model(run_id, agent_roles)
    if harness == "openclaw":
        return _detect_openclaw_model(run_id, agent_roles)
    return None


def _rewrite_trace_jsonl(
    trace_path: Path,
    *,
    old_run_id: str,
    new_run_id: str,
    observed_model: str,
) -> None:
    updated_lines: list[str] = []
    with open(trace_path) as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            if payload.get("trace_id") == old_run_id:
                payload["trace_id"] = new_run_id
            if payload.get("type") == "trace_start":
                payload["model"] = observed_model
            updated_lines.append(json.dumps(payload, ensure_ascii=False) + "\n")
    trace_path.write_text("".join(updated_lines))


def canonicalize_clawteam_artifacts(
    *,
    trace_path: Path,
    run_id: str,
    harness: str,
    agent_roles: Iterable[str],
    results_dir: Path | None,
    task_id: str,
    current_llm: str,
    artifact_prefix: str | None = None,
) -> tuple[Path, str, str]:
    """Rewrite trace metadata and rename artifact paths using the observed model."""
    observed_model = detect_clawteam_backbone(harness, run_id, agent_roles)
    if not observed_model:
        return trace_path, run_id, current_llm

    canonical_run_id = replace_run_id_model(run_id, observed_model)
    if canonical_run_id == run_id:
        return trace_path, run_id, observed_model

    _rewrite_trace_jsonl(
        trace_path,
        old_run_id=run_id,
        new_run_id=canonical_run_id,
        observed_model=observed_model,
    )
    prefix = artifact_prefix or task_id
    canonical_trace_path = trace_path.with_name(f"{prefix}_{canonical_run_id}.jsonl")
    trace_path.replace(canonical_trace_path)

    if results_dir:
        for suffix in (".json", ".sqlite"):
            old_path = results_dir / f"{prefix}_{run_id}{suffix}"
            new_path = results_dir / f"{prefix}_{canonical_run_id}{suffix}"
            if old_path.exists() and old_path != new_path:
                old_path.replace(new_path)

    return canonical_trace_path, canonical_run_id, observed_model
