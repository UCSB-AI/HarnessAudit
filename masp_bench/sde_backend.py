"""Real SDE tool backend — actual file I/O, test execution, and code search.

This module implements the ``backend_type: real`` dispatch for the
software_engineering domain.  Each tool operates on an isolated
workspace directory (created by :class:`~masp_bench.workspace.WorkspaceManager`).

Security: all file paths are resolved and checked to be within the workspace
root to prevent path-traversal escapes.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path


def dispatch_real_tool(name: str, args: dict, ws: Path) -> str:
    """Route a real-backend SDE tool call to the appropriate handler."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return f"ERROR: unknown real backend tool: {name}"
    try:
        return handler(args, ws)
    except Exception as exc:
        return f"ERROR ({name}): {exc}"


# ---------------------------------------------------------------------------
# Individual tool handlers
# ---------------------------------------------------------------------------


def _read_source_code(args: dict, ws: Path) -> str:
    file_path = args.get("file_path", "")
    if not file_path:
        return "ERROR: file_path is required"
    target = _safe_resolve(ws, file_path)
    if target is None:
        return f"ERROR: path outside workspace: {file_path}"
    if not target.exists():
        return f"ERROR: file not found: {file_path}"
    if target.is_dir():
        # List directory contents
        entries = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())
        return f"Directory listing of {file_path}:\n" + "\n".join(entries)
    content = target.read_text(errors="replace")
    # Add line numbers for readability
    lines = content.splitlines()
    numbered = [f"{i + 1:4d} | {line}" for i, line in enumerate(lines)]
    return "\n".join(numbered)


def _write_source_code(args: dict, ws: Path) -> str:
    file_path = args.get("file_path", "")
    content = args.get("content", "")
    mode = args.get("mode", "overwrite")
    if not file_path:
        return "ERROR: file_path is required"
    target = _safe_resolve(ws, file_path)
    if target is None:
        return f"ERROR: path outside workspace: {file_path}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if mode == "append":
        existing = target.read_text(errors="replace") if target.exists() else ""
        target.write_text(existing + content)
    else:
        target.write_text(content)
    return f"OK: wrote {len(content)} bytes to {file_path}"


def _run_tests(args: dict, ws: Path) -> str:
    test_path = args.get("test_path", "tests/")
    flags_str = args.get("flags", "")
    flags = shlex.split(flags_str) if flags_str else []
    cmd = ["python", "-m", "pytest", test_path, *flags]
    try:
        res = subprocess.run(
            cmd,
            cwd=ws,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return "ERROR: test execution timed out (120s)"
    stdout = res.stdout[-3000:] if len(res.stdout) > 3000 else res.stdout
    stderr = res.stderr[-1000:] if len(res.stderr) > 1000 else res.stderr
    return f"exit_code={res.returncode}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}"


def _search_codebase(args: dict, ws: Path) -> str:
    pattern = args.get("pattern", "")
    file_glob = args.get("file_glob", "*.py")
    if not pattern:
        return "ERROR: pattern is required"
    cmd = ["grep", "-rn", "--include", file_glob, pattern, "."]
    try:
        res = subprocess.run(
            cmd,
            cwd=ws,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return "ERROR: search timed out (30s)"
    output = res.stdout[:4000] if len(res.stdout) > 4000 else res.stdout
    if not output:
        return "No matches found."
    return output


def _read_env_secrets(args: dict, ws: Path) -> str:
    env_file = args.get("env_file", ".env")
    target = _safe_resolve(ws, env_file)
    if target is None:
        return f"ERROR: path outside workspace: {env_file}"
    if not target.exists():
        return f"ERROR: file not found: {env_file}"
    return target.read_text(errors="replace")


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


def _safe_resolve(ws: Path, relative: str) -> Path | None:
    """Resolve *relative* within *ws* and reject path-traversal escapes."""
    resolved = (ws / relative).resolve()
    if not resolved.is_relative_to(ws.resolve()):
        return None
    return resolved


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------


_HANDLERS: dict[str, callable] = {
    "read_source_code": _read_source_code,
    "write_source_code": _write_source_code,
    "run_tests": _run_tests,
    "search_codebase": _search_codebase,
    "read_env_secrets": _read_env_secrets,
}
