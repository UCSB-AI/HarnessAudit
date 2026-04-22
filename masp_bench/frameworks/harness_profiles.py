"""Harness profile definitions for multi-harness ClawTeam adapter.

Each profile describes static metadata for a harness (Claude Code, Codex,
OpenClaw).  Runtime behavior (MCP config writing, trace collection) lives
in the adapter and per-harness ingestor modules — not here.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib


@dataclass(frozen=True)
class HarnessProfile:
    """Static metadata for a harness."""

    name: str
    """Short identifier: ``"claude"``, ``"codex"``, ``"openclaw"``."""

    spawn_command: tuple[str, ...]
    """Base CLI command passed to ``SubprocessBackend.spawn(command=...)``.

    ``NativeCliAdapter.prepare_command()`` handles flag injection (prompt,
    skip-permissions, workspace, etc.) based on the executable name.
    """

    mcp_transport: str
    """MCP transport type for the bank server: ``"sse"`` or ``"streamable-http"``."""

    mcp_config_scope: str
    """Where MCP config is written: ``"global"`` (Claude Code ~/.claude.json)
    or ``"project"`` (config file in agent cwd)."""

    run_id_slug: str
    """Slug embedded in run IDs: ``ct-{slug}-{model}-{uuid}``."""


# ── Registry ─────────────────────────────────────────────────────────

PROFILES: dict[str, HarnessProfile] = {
    "claude": HarnessProfile(
        name="claude",
        spawn_command=("claude",),
        mcp_transport="sse",
        mcp_config_scope="global",
        run_id_slug="claude",
    ),
    "codex": HarnessProfile(
        name="codex",
        spawn_command=("codex", "exec"),
        mcp_transport="streamable-http",  # Codex rmcp uses streamable-http (verified working in run 863f3867)
        mcp_config_scope="global",
        run_id_slug="codex",
    ),
    "openclaw": HarnessProfile(
        name="openclaw",
        spawn_command=("openclaw", "agent"),
        mcp_transport="sse",
        mcp_config_scope="project",
        run_id_slug="openclaw",
    ),
}


def get_harness_profile(name: str) -> HarnessProfile:
    """Look up a harness profile by name.

    Raises ``ValueError`` with a helpful message if the name is unknown.
    """
    try:
        return PROFILES[name]
    except KeyError:
        choices = ", ".join(sorted(PROFILES))
        raise ValueError(
            f"Unknown harness {name!r}.  Choose from: {choices}"
        ) from None


def session_name(run_id: str, agent_name: str, harness: str) -> str:
    """Return a run-scoped session identifier for artifact isolation.

    OpenClaw's ``NativeCliAdapter`` defaults ``--session-id`` to
    ``agent_name``, which causes collisions across runs. This helper
    produces a run-scoped identifier so every run gets its own artifact
    namespace.

    OpenClaw also forwards ``sessionId`` as OpenAI's
    ``prompt_cache_key``. That field is capped at 64 characters, so
    long role names such as ``payments_and_coverage_specialist`` need
    a stable shortened form.

    For Claude Code this is a no-op — session IDs are auto-generated.
    For Codex this maps to thread naming.
    """
    if harness == "claude":
        return agent_name  # Claude Code auto-generates session IDs

    candidate = f"{run_id}-{agent_name}"
    if harness != "openclaw" or len(candidate) <= 64:
        return candidate

    digest = hashlib.sha1(candidate.encode("utf-8")).hexdigest()[:8]
    readable_prefix = candidate[:55]
    return f"{readable_prefix}-{digest}"
