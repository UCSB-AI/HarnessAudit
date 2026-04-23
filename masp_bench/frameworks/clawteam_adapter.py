"""ClawTeam adapter for MASP_Bench — spawns independent agent processes.

Each task agent becomes a real CLI agent process (Claude Code, Codex, or
OpenClaw) coordinated by ClawTeam.  Domain tools are exposed via a shared
MCP server.  After all agents finish, their session logs are parsed into
``ObservableAction`` entries for violation checking.

The ``--harness`` flag selects which CLI harness to use.  ClawTeam's
``NativeCliAdapter`` handles per-harness command construction.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from .base import MASFrameworkAdapter, RunContext, RunOutcome
from .common import (
    build_clawteam_hub_prompt,
    build_clawteam_spoke_prompt,
    build_clawteam_spoke_task_description,
)
from .harness_profiles import HarnessProfile, get_harness_profile
from .harness_runtime import HarnessRuntime, get_harness_runtime

log = logging.getLogger(__name__)

DEFAULT_AGENT_TIMEOUT = 300
SENTINEL_SUBJECT = "[SENTINEL] Mark completed when final output is ready"


def _spoke_task_subject(agent_cfg: Any) -> str:
    summary = (agent_cfg.description or agent_cfg.system_prompt or "").strip()
    if summary and not summary.endswith("."):
        summary = f"{summary}."
    if not summary:
        summary = "Complete your assigned specialist work."
    return f"{agent_cfg.role}: {summary}"


def _spoke_task_description(
    agent_cfg: Any,
    task_goal: str,
    hub_role: str,
    *,
    team_name: str,
) -> str:
    return build_clawteam_spoke_task_description(
        agent_cfg,
        task_goal,
        hub_role,
        team_name=team_name,
    )


def seed_spoke_tasks(
    task_store: Any,
    spoke_agents: list[Any],
    *,
    task_goal: str,
    hub_role: str,
    team_name: str,
) -> dict[str, str]:
    """Create one seeded task-board assignment per specialist."""
    task_ids: dict[str, str] = {}
    for agent_cfg in spoke_agents:
        task = task_store.create(
            subject=_spoke_task_subject(agent_cfg),
            description=_spoke_task_description(
                agent_cfg,
                task_goal,
                hub_role,
                team_name=team_name,
            ),
            owner=agent_cfg.role,
            metadata={
                "assignment_mode": "task_board_first",
                "hub_role": hub_role,
                "role": agent_cfg.role,
            },
        )
        task_ids[agent_cfg.role] = task.id
    return task_ids


class ClawTeamAdapter(MASFrameworkAdapter):
    """Run tasks by spawning ClawTeam-managed agent processes."""

    framework_id = "clawteam"

    def __init__(
        self,
        agent_timeout: int = DEFAULT_AGENT_TIMEOUT,
        harness: str = "claude",
    ) -> None:
        self._agent_timeout = agent_timeout
        self._profile: HarnessProfile = get_harness_profile(harness)
        self._runtime: HarnessRuntime = get_harness_runtime(harness)

    # ------------------------------------------------------------------
    # MASFrameworkAdapter protocol
    # ------------------------------------------------------------------

    async def run(
        self, ctx: RunContext, initial_input: str | list[dict[str, Any]]
    ) -> RunOutcome:
        try:
            from clawteam.spawn.subprocess_backend import SubprocessBackend
            from clawteam.store.file import FileTaskStore
            from clawteam.team.manager import TeamManager
        except ImportError as exc:
            raise RuntimeError(
                "ClawTeam is not installed.  pip install -e /path/to/ClawTeam"
            ) from exc

        team_name = f"masp-{ctx.run_id}"
        self._current_run_id = ctx.run_id
        mcp_proc: subprocess.Popen | None = None
        bank_dump_path = Path(f"/tmp/masp_bank_{ctx.run_id}.db")
        final_text = ""
        worktrees: dict[str, Path] = {}
        expected_agent_count = len(ctx.task.agents)

        try:
            # 0. Clean up stale team from previous failed run
            if TeamManager.team_exists(team_name):
                log.warning("Stale team %s found, cleaning up", team_name)
                TeamManager.cleanup(team_name)

            # 1. Start domain MCP server (transport per harness profile)
            mcp_proc, mcp_port = await self._start_mcp_server(ctx, bank_dump_path)
            log.info("MCP server started on port %d", mcp_port)

            # 2. Create ClawTeam team
            hub_cfg = ctx.task.agents[0]
            hub_id = uuid.uuid4().hex[:12]
            hub_role = hub_cfg.role
            TeamManager.create_team(
                name=team_name,
                leader_name=hub_role,
                leader_id=hub_id,
                description=f"MASP run {ctx.run_id} — {ctx.task.task_id}",
            )
            from .common import extract_input_text

            input_text = extract_input_text(initial_input)

            # 3. Create sentinel task for hub completion detection
            task_store = FileTaskStore(team_name)
            sentinel = task_store.create(
                subject=SENTINEL_SUBJECT,
                description="Hub must mark this completed after producing final output.",
                owner=hub_role,
            )
            sentinel_id = sentinel.id
            log.info("Sentinel task created: %s", sentinel_id)

            seeded_task_ids = seed_spoke_tasks(
                task_store,
                ctx.task.agents[1:],
                task_goal=input_text or ctx.task.goal,
                hub_role=hub_role,
                team_name=team_name,
            )
            log.info("Seeded %d spoke tasks on the shared task board", len(seeded_task_ids))

            # 4. Setup working directories (mkdir only, no MCP write yet)
            clawteam_mcp_bin = shutil.which("clawteam-mcp") or "clawteam-mcp"
            for agent_cfg in ctx.task.agents:
                wt_path = self._make_agent_dir(team_name, agent_cfg.role)
                worktrees[agent_cfg.role] = wt_path

            # 5. Register MCP for ALL agents (per-harness config strategy)
            self._register_mcp_for_harness(
                worktrees, mcp_port, clawteam_mcp_bin,
            )

            # 6. Spawn all agents — record timestamp for JSONL filtering
            import time as _time

            spawn_timestamp = _time.time()
            backend = SubprocessBackend()
            all_agents = ctx.task.agents

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
                        hub_role, all_agents[1:],
                        input_text or ctx.task.goal,
                        team_name=team_name,
                        sentinel_task_id=sentinel_id,
                    )
                else:
                    prompt = build_clawteam_spoke_prompt(
                        agent_cfg,
                        hub_role,
                        team_name=team_name,
                    )

                sys_prompt = agent_cfg.system_prompt or agent_cfg.description or ""

                cwd = str(worktrees[agent_cfg.role])
                spawn_env = self._build_spawn_env(agent_cfg.role, cwd)
                spawn_command = list(self._profile.spawn_command)
                runtime_session_id = self._runtime.session_id(
                    run_id=ctx.run_id,
                    agent_role=agent_cfg.role,
                )
                if runtime_session_id:
                    spawn_command.extend(["--session-id", runtime_session_id])
                result_msg = backend.spawn(
                    command=spawn_command,
                    agent_name=agent_cfg.role,
                    agent_id=agent_id,
                    agent_type=agent_cfg.description or agent_cfg.role,
                    team_name=team_name,
                    prompt=prompt,
                    env=spawn_env,
                    cwd=cwd,
                    skip_permissions=True,
                    system_prompt=sys_prompt,
                    is_leader=is_leader,
                    keepalive=False,
                )
                log.info("Spawned %s (%s): %s",
                         agent_cfg.role, self._profile.name, result_msg)

            # 7. Wait for completion (sentinel task OR PID death OR timeout)
            await self._wait_for_agents(
                team_name,
                expected_count=expected_agent_count,
                sentinel_task_id=sentinel_id,
                timeout=self._agent_timeout,
            )

            # 8. Stop MCP server and restore Bank state
            self._stop_mcp_server(mcp_proc)
            mcp_proc = None
            self._restore_bank(ctx.bank, bank_dump_path)

            # 9. Collect traces (per-harness ingestor)
            catalog_names = {t.name for t in ctx.catalog.tools}
            all_roles = [a.role for a in all_agents]

            final_text = self._collect_traces(
                worktrees=worktrees,
                catalog_names=catalog_names,
                all_roles=all_roles,
                action_sink=ctx.action_sink,
                hub_role=hub_role,
                spawn_timestamp=spawn_timestamp,
            )

            # 10. Emit final user_output
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
            self._unregister_mcp_for_harness(worktrees)
            self._cleanup_team(team_name)
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
            "--transport", self._profile.mcp_transport,
            "--task-metadata-json", task_metadata_json,
            "--bank-dump-path", str(bank_dump_path),
        ]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )

        loop = asyncio.get_running_loop()
        port = 0
        stdout = proc.stdout
        if stdout is None:
            proc.kill()
            raise RuntimeError("MCP server subprocess has no stdout pipe")
        try:
            line = await asyncio.wait_for(
                loop.run_in_executor(None, stdout.readline),
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
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    @staticmethod
    def _restore_bank(bank: Any, dump_path: Path) -> None:
        """Restore MCP server's Bank state into the Runner's Bank.

        .. note::

            ``ctx.bank`` is empty until this method runs.  Do NOT query
            ``ctx.bank`` inside the adapter before ``_restore_bank`` is called.
        """
        if bank is None or not dump_path.exists():
            return
        if not hasattr(bank, "conn"):
            return
        try:
            import sqlite3

            src = sqlite3.connect(str(dump_path))
            integrity = src.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                log.warning("Bank dump integrity check failed: %s", integrity)
                src.close()
                return
            src.backup(bank.conn)
            src.close()
            log.info("Bank state restored from %s", dump_path)
        except Exception:
            log.warning("Failed to restore bank from %s", dump_path, exc_info=True)

    # ------------------------------------------------------------------
    # Agent directory setup
    # ------------------------------------------------------------------

    @staticmethod
    def _make_agent_dir(team_name: str, agent_role: str) -> Path:
        """Create a working directory for the agent (MCP registered separately)."""
        from clawteam.team.models import get_data_dir

        wt_path = get_data_dir() / "workspaces" / team_name / agent_role
        wt_path.mkdir(parents=True, exist_ok=True)
        return wt_path

    # ------------------------------------------------------------------
    # Agent completion detection
    # ------------------------------------------------------------------

    @staticmethod
    async def _wait_for_agents(
        team_name: str,
        *,
        expected_count: int,
        sentinel_task_id: str,
        timeout: int = DEFAULT_AGENT_TIMEOUT,
    ) -> None:
        """Wait until hub completes (sentinel task) or all PIDs die or timeout.

        Priority order:
        1. Sentinel task marked completed → wait 15s for spokes to wrap up → return
        2. All agent PIDs dead → return immediately
        3. Timeout → return
        """
        from clawteam.spawn.registry import get_registry, is_agent_alive
        from clawteam.store.file import FileTaskStore
        from clawteam.team.models import TaskStatus

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        poll_interval = 5

        # Initial grace period for agents to register
        await asyncio.sleep(3)

        while loop.time() < deadline:
            # --- Check 1: sentinel task completed ---
            try:
                store = FileTaskStore(team_name)
                task_item = store.get(sentinel_task_id)
                if task_item and task_item.status == TaskStatus.completed:
                    log.info("Sentinel task completed — hub finished, "
                             "giving spokes 15s to wrap up")
                    await asyncio.sleep(15)
                    return
            except Exception:
                pass

            # --- Check 2: all PIDs dead ---
            registry = get_registry(team_name)
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
    # Per-harness MCP registration
    # ------------------------------------------------------------------

    def _register_mcp_for_harness(
        self,
        worktrees: dict[str, Path],
        mcp_port: int,
        clawteam_mcp_bin: str,
    ) -> None:
        self._runtime.register_mcp(
            worktrees=worktrees,
            mcp_port=mcp_port,
            clawteam_command=clawteam_mcp_bin,
        )

    def _unregister_mcp_for_harness(self, worktrees: dict[str, Path]) -> None:
        self._runtime.unregister_mcp(worktrees=worktrees)

    # ------------------------------------------------------------------
    # Per-harness spawn environment
    # ------------------------------------------------------------------

    def _build_spawn_env(self, agent_role: str, cwd: str) -> dict[str, str]:
        return self._runtime.build_spawn_env(agent_role=agent_role, cwd=cwd)

    # ------------------------------------------------------------------
    # Per-harness trace collection
    # ------------------------------------------------------------------

    def _collect_traces(
        self,
        worktrees: dict[str, Path],
        catalog_names: set[str],
        all_roles: list[str],
        action_sink: Any,
        hub_role: str,
        spawn_timestamp: float,
    ) -> str:
        return self._runtime.collect_traces(
            run_id=self._current_run_id,
            worktrees=worktrees,
            catalog_names=catalog_names,
            all_roles=all_roles,
            action_sink=action_sink,
            hub_role=hub_role,
            spawn_timestamp=spawn_timestamp,
        )

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def _cleanup_team(self, team_name: str) -> None:
        try:
            from clawteam.team.manager import TeamManager
            TeamManager.cleanup(team_name)
        except Exception:
            log.debug("ClawTeam cleanup failed for team %s", team_name, exc_info=True)
        self._runtime.cleanup()
