"""ClawTeam adapter for MASP_Bench — spawns independent Claude Code agents.

Each task agent becomes a real Claude Code process coordinated by ClawTeam.
Domain tools are exposed via a shared MCP server (SSE).  After all agents
finish, their JSONL session logs are parsed into ``ObservableAction`` entries
for violation checking.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .base import MASFrameworkAdapter, RunContext, RunOutcome
from .common import build_clawteam_hub_prompt, build_clawteam_spoke_prompt
from .jsonl_collector import collect_all_agent_traces

log = logging.getLogger(__name__)

# Default timeout (seconds) waiting for all agents to exit.
DEFAULT_AGENT_TIMEOUT = 600


class ClawTeamAdapter(MASFrameworkAdapter):
    """Run tasks by spawning ClawTeam-managed Claude Code agents."""

    framework_id = "clawteam"

    def __init__(self, agent_timeout: int = DEFAULT_AGENT_TIMEOUT) -> None:
        self._agent_timeout = agent_timeout

    # ------------------------------------------------------------------
    # MASFrameworkAdapter protocol
    # ------------------------------------------------------------------

    async def run(
        self, ctx: RunContext, initial_input: str | list[dict[str, Any]]
    ) -> RunOutcome:
        # Lazy-import ClawTeam to keep it an optional dependency.
        try:
            from clawteam.spawn.subprocess_backend import SubprocessBackend
            from clawteam.spawn.registry import is_agent_alive, get_registry
            from clawteam.team.manager import TeamManager
        except ImportError as exc:
            raise RuntimeError(
                "ClawTeam is not installed.  pip install -e /path/to/ClawTeam"
            ) from exc

        team_name = f"masp-{ctx.run_id}"
        mcp_proc: subprocess.Popen | None = None
        final_text = ""

        try:
            # 1. Start domain MCP server (SSE transport)
            mcp_proc, mcp_port = await self._start_mcp_server(ctx)
            log.info("MCP server started on port %d", mcp_port)

            # 2. Create ClawTeam team
            hub_cfg = ctx.task.agents[0]
            hub_id = uuid.uuid4().hex[:12]
            TeamManager.create_team(
                name=team_name,
                leader_name=hub_cfg.role,
                leader_id=hub_id,
                description=f"MASP run {ctx.run_id} — {ctx.task.task_id}",
            )

            # 3. Setup worktrees and .claude.json per agent
            worktrees: dict[str, Path] = {}
            for agent_cfg in ctx.task.agents:
                wt_path = self._setup_agent_dir(team_name, agent_cfg.role, mcp_port)
                worktrees[agent_cfg.role] = wt_path

            # 4. Spawn all agents
            backend = SubprocessBackend()
            all_agents = ctx.task.agents
            hub_role = hub_cfg.role

            for idx, agent_cfg in enumerate(all_agents):
                is_leader = idx == 0
                agent_id = hub_id if is_leader else uuid.uuid4().hex[:12]

                # Register spoke members
                if not is_leader:
                    TeamManager.add_member(
                        team_name,
                        agent_cfg.role,
                        agent_id,
                        agent_type=agent_cfg.description or "specialist",
                    )

                # Build prompt
                if is_leader:
                    prompt = build_clawteam_hub_prompt(
                        hub_role, all_agents[1:], ctx.task.goal,
                    )
                else:
                    prompt = build_clawteam_spoke_prompt(agent_cfg, hub_role)

                # Build system prompt (role definition)
                sys_prompt = agent_cfg.system_prompt or agent_cfg.description or ""

                cwd = str(worktrees[agent_cfg.role])
                result_msg = backend.spawn(
                    command=["claude"],
                    agent_name=agent_cfg.role,
                    agent_id=agent_id,
                    agent_type=agent_cfg.description or agent_cfg.role,
                    team_name=team_name,
                    prompt=prompt,
                    cwd=cwd,
                    skip_permissions=True,
                    system_prompt=sys_prompt,
                    is_leader=is_leader,
                    keepalive=False,
                )
                log.info("Spawned %s: %s", agent_cfg.role, result_msg)

            # 5. Wait for all agents to exit
            await self._wait_for_agents(team_name, timeout=self._agent_timeout)

            # 6. Collect JSONL traces
            catalog_names = {t.name for t in ctx.catalog.tools}
            all_roles = [a.role for a in all_agents]

            final_text = collect_all_agent_traces(
                worktree_map=worktrees,
                catalog_tool_names=catalog_names,
                all_agent_roles=all_roles,
                action_sink=ctx.action_sink,
                hub_role=hub_role,
            )

            # 7. Emit final user_output
            ctx.action_sink.emit_communication(
                agent_id=hub_role,
                agent_role=hub_role,
                target_agent="user",
                target_role="user",
                content=final_text,
                raw_event={
                    "framework": "clawteam",
                    "communication_kind": "user_output",
                    "capture_mode": "jsonl_hub_final_text",
                },
                kind="user_output",
            )

            return RunOutcome(framework=self.framework_id, final_output=final_text)

        except Exception as exc:
            log.exception("ClawTeam adapter error")
            return RunOutcome(
                framework=self.framework_id,
                final_output=final_text,
                error=str(exc),
            )
        finally:
            # 8. Cleanup
            self._stop_mcp_server(mcp_proc)
            self._cleanup_team(team_name)

    # ------------------------------------------------------------------
    # MCP server lifecycle
    # ------------------------------------------------------------------

    async def _start_mcp_server(
        self, ctx: RunContext
    ) -> tuple[subprocess.Popen, int]:
        """Start the domain MCP server as a subprocess, return (proc, port)."""
        task_metadata_json = json.dumps(ctx.task.metadata) if ctx.task.metadata else "{}"

        cmd = [
            sys.executable,
            "-m",
            "masp_bench.frameworks.mcp_bank_server",
            "--domain",
            ctx.task.domain,
            "--port",
            "0",
            "--task-metadata-json",
            task_metadata_json,
        ]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Read the PORT=<n> line from stdout (with timeout).
        port = 0
        try:
            line = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None, proc.stdout.readline  # type: ignore[union-attr]
                ),
                timeout=30,
            )
            if line and line.startswith("PORT="):
                port = int(line.strip().split("=", 1)[1])
        except (asyncio.TimeoutError, ValueError) as exc:
            proc.kill()
            raise RuntimeError(f"Failed to read MCP server port: {exc}") from exc

        if port == 0:
            proc.kill()
            raise RuntimeError("MCP server did not report a port")

        # Give the server a moment to bind.
        await asyncio.sleep(0.5)
        return proc, port

    @staticmethod
    def _stop_mcp_server(proc: subprocess.Popen | None) -> None:
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Agent directory / worktree setup
    # ------------------------------------------------------------------

    def _setup_agent_dir(
        self, team_name: str, agent_role: str, mcp_port: int
    ) -> Path:
        """Create a working directory for the agent with .claude.json MCP config."""
        from clawteam.team.models import get_data_dir

        base = get_data_dir() / "workspaces" / team_name
        base.mkdir(parents=True, exist_ok=True)
        wt_path = base / agent_role
        wt_path.mkdir(parents=True, exist_ok=True)

        # Write .claude.json (project-local MCP config, scope=local)
        claude_json = {
            "mcpServers": {
                "masp_tools": {
                    "type": "sse",
                    "url": f"http://127.0.0.1:{mcp_port}/sse",
                }
            }
        }
        (wt_path / ".claude.json").write_text(
            json.dumps(claude_json, indent=2), encoding="utf-8"
        )

        return wt_path

    # ------------------------------------------------------------------
    # Agent completion detection
    # ------------------------------------------------------------------

    async def _wait_for_agents(
        self, team_name: str, *, timeout: int = DEFAULT_AGENT_TIMEOUT
    ) -> None:
        """Poll spawn registry until all agents are dead or timeout expires."""
        from clawteam.spawn.registry import get_registry, is_agent_alive

        deadline = asyncio.get_event_loop().time() + timeout
        poll_interval = 5

        while asyncio.get_event_loop().time() < deadline:
            registry = get_registry(team_name)
            if not registry:
                return

            all_dead = all(
                is_agent_alive(team_name, name) is not True
                for name in registry
            )
            if all_dead:
                log.info("All agents have exited for team %s", team_name)
                return

            await asyncio.sleep(poll_interval)

        log.warning("Timeout (%ds) waiting for agents in team %s", timeout, team_name)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    @staticmethod
    def _cleanup_team(team_name: str) -> None:
        """Best-effort cleanup of the ClawTeam team and its data."""
        try:
            from clawteam.team.manager import TeamManager

            TeamManager.cleanup(team_name)
        except Exception:
            log.debug("ClawTeam cleanup failed for team %s", team_name, exc_info=True)
