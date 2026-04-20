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
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from .base import MASFrameworkAdapter, RunContext, RunOutcome
from .common import build_clawteam_hub_prompt, build_clawteam_spoke_prompt, build_task_input
from .jsonl_collector import collect_all_agent_traces

log = logging.getLogger(__name__)

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
        try:
            from clawteam.spawn.subprocess_backend import SubprocessBackend
            from clawteam.team.manager import TeamManager
        except ImportError as exc:
            raise RuntimeError(
                "ClawTeam is not installed.  pip install -e /path/to/ClawTeam"
            ) from exc

        team_name = f"masp-{ctx.run_id}"
        mcp_proc: subprocess.Popen | None = None
        bank_dump_path = Path(f"/tmp/masp_bank_{ctx.run_id}.db")
        final_text = ""
        expected_agent_count = len(ctx.task.agents)

        try:
            # 1. Start domain MCP server (SSE transport)
            mcp_proc, mcp_port = await self._start_mcp_server(ctx, bank_dump_path)
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

            # 3. Setup working directories + .claude.json per agent
            worktrees: dict[str, Path] = {}
            for agent_cfg in ctx.task.agents:
                wt_path = self._setup_agent_dir(team_name, agent_cfg.role, mcp_port)
                worktrees[agent_cfg.role] = wt_path

            # 4. Spawn all agents
            backend = SubprocessBackend()
            all_agents = ctx.task.agents
            hub_role = hub_cfg.role

            # Build text version of initial_input for hub prompt (fix #6)
            input_text = self._extract_input_text(initial_input)

            for idx, agent_cfg in enumerate(all_agents):
                is_leader = idx == 0
                agent_id = hub_id if is_leader else uuid.uuid4().hex[:12]

                if not is_leader:
                    TeamManager.add_member(
                        team_name,
                        agent_cfg.role,
                        agent_id,
                        agent_type=agent_cfg.description or "specialist",
                    )

                if is_leader:
                    prompt = build_clawteam_hub_prompt(
                        hub_role, all_agents[1:], input_text or ctx.task.goal,
                    )
                else:
                    prompt = build_clawteam_spoke_prompt(agent_cfg, hub_role)

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

            # 5. Wait for all agents to exit (fix #2: pass expected count)
            await self._wait_for_agents(
                team_name,
                expected_count=expected_agent_count,
                timeout=self._agent_timeout,
            )

            # 6. Stop MCP server and restore Bank state (fix #1)
            self._stop_mcp_server(mcp_proc)
            mcp_proc = None  # prevent double-stop in finally
            self._restore_bank(ctx.bank, bank_dump_path)

            # 7. Collect JSONL traces
            catalog_names = {t.name for t in ctx.catalog.tools}
            all_roles = [a.role for a in all_agents]

            final_text = collect_all_agent_traces(
                worktree_map=worktrees,
                catalog_tool_names=catalog_names,
                all_agent_roles=all_roles,
                action_sink=ctx.action_sink,
                hub_role=hub_role,
            )

            # 8. Emit final user_output
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
            self._stop_mcp_server(mcp_proc)
            self._cleanup_team(team_name)
            # Clean up dump file
            try:
                bank_dump_path.unlink(missing_ok=True)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # MCP server lifecycle
    # ------------------------------------------------------------------

    async def _start_mcp_server(
        self, ctx: RunContext, bank_dump_path: Path
    ) -> tuple[subprocess.Popen, int]:
        """Start the domain MCP server as a subprocess, return (proc, port)."""
        task_metadata_json = json.dumps(ctx.task.metadata) if ctx.task.metadata else "{}"

        cmd = [
            sys.executable, "-m", "masp_bench.frameworks.mcp_bank_server",
            "--domain", ctx.task.domain,
            "--port", "0",
            "--task-metadata-json", task_metadata_json,
            "--bank-dump-path", str(bank_dump_path),
        ]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,  # fix #4: avoid pipe deadlock
            text=True,
        )

        # Read the PORT=<n> line from stdout.
        loop = asyncio.get_running_loop()  # fix #9
        port = 0
        try:
            line = await asyncio.wait_for(
                loop.run_in_executor(None, proc.stdout.readline),
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

        await asyncio.sleep(0.5)
        return proc, port

    @staticmethod
    def _stop_mcp_server(proc: subprocess.Popen | None) -> None:
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=10)  # allow time for Bank dump
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    @staticmethod
    def _restore_bank(bank: Any, dump_path: Path) -> None:
        """Restore MCP server's Bank state into the Runner's Bank (fix #1)."""
        if bank is None or not dump_path.exists():
            return
        if not hasattr(bank, "conn"):
            return
        try:
            import sqlite3

            src = sqlite3.connect(str(dump_path))
            src.backup(bank.conn)
            src.close()
            log.info("Bank state restored from %s", dump_path)
        except Exception:
            log.warning("Failed to restore bank from %s", dump_path, exc_info=True)

    # ------------------------------------------------------------------
    # Agent directory setup
    # ------------------------------------------------------------------

    @staticmethod
    def _setup_agent_dir(team_name: str, agent_role: str, mcp_port: int) -> Path:
        """Create a working directory for the agent with .claude.json MCP config."""
        from clawteam.team.models import get_data_dir

        base = get_data_dir() / "workspaces" / team_name
        base.mkdir(parents=True, exist_ok=True)
        wt_path = base / agent_role
        wt_path.mkdir(parents=True, exist_ok=True)

        # .claude.json = project-local MCP config (scope=local)
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

    @staticmethod
    async def _wait_for_agents(
        team_name: str,
        *,
        expected_count: int,
        timeout: int = DEFAULT_AGENT_TIMEOUT,
    ) -> None:
        """Poll spawn registry until all agents are dead or timeout expires.

        Waits for the registry to contain ``expected_count`` entries before
        starting liveness checks (fix #2: prevents premature return on empty
        registry).
        """
        from clawteam.spawn.registry import get_registry, is_agent_alive

        loop = asyncio.get_running_loop()  # fix #9
        deadline = loop.time() + timeout
        poll_interval = 5

        # Initial grace period for agents to register
        await asyncio.sleep(3)

        while loop.time() < deadline:
            registry = get_registry(team_name)

            # Don't declare "all done" until registry has everyone
            if len(registry) >= expected_count:
                all_dead = all(
                    is_agent_alive(team_name, name) is not True
                    for name in registry
                )
                if all_dead:
                    log.info("All %d agents have exited for team %s",
                             len(registry), team_name)
                    return

            await asyncio.sleep(poll_interval)

        log.warning("Timeout (%ds) waiting for agents in team %s", timeout, team_name)

    # ------------------------------------------------------------------
    # Input text extraction (fix #6)
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_input_text(initial_input: str | list[dict[str, Any]]) -> str:
        """Extract text content from initial_input for hub prompt."""
        if isinstance(initial_input, str):
            return initial_input
        parts: list[str] = []
        for msg in initial_input:
            content = msg.get("content", [])
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "input_text":
                        text = str(item.get("text", "")).strip()
                        if text:
                            parts.append(text)
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    @staticmethod
    def _cleanup_team(team_name: str) -> None:
        try:
            from clawteam.team.manager import TeamManager
            TeamManager.cleanup(team_name)
        except Exception:
            log.debug("ClawTeam cleanup failed for team %s", team_name, exc_info=True)
