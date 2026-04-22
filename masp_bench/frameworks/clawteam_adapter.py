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
import fcntl
import json
import logging
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from .base import MASFrameworkAdapter, RunContext, RunOutcome
from .common import build_clawteam_hub_prompt, build_clawteam_spoke_prompt
from .harness_profiles import HarnessProfile, get_harness_profile, session_name

log = logging.getLogger(__name__)

DEFAULT_AGENT_TIMEOUT = 300
SENTINEL_SUBJECT = "[SENTINEL] Mark completed when final output is ready"

_CLAUDE_JSON = Path.home() / ".claude.json"
_MCP_SERVER_NAMES = ("masp_tools", "clawteam")


# ---------------------------------------------------------------------------
# Atomic ~/.claude.json read-modify-write (file-locked)
# ---------------------------------------------------------------------------

def _locked_update_claude_json(mutator: Any) -> None:
    """Read ~/.claude.json, apply *mutator(cfg)*, write back — under flock.

    This prevents races with Claude Code or other adapter instances that
    may also be reading/writing the same file.
    """
    _CLAUDE_JSON.touch(exist_ok=True)
    with open(_CLAUDE_JSON, "r+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            raw = f.read()
            cfg = json.loads(raw) if raw.strip() else {}
            mutator(cfg)
            f.seek(0)
            f.truncate()
            f.write(json.dumps(cfg, indent=2))
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _register_mcp_batch(
    registrations: list[tuple[str, dict[str, dict[str, Any]]]]
) -> None:
    """Atomically register MCP servers for multiple projects in one write."""

    def _mutate(cfg: dict[str, Any]) -> None:
        projects = cfg.setdefault("projects", {})
        for project_path, mcp_servers in registrations:
            project = projects.setdefault(project_path, {})
            servers = project.setdefault("mcpServers", {})
            servers.update(mcp_servers)

    _locked_update_claude_json(_mutate)


def _unregister_mcp_batch(
    project_paths: list[str], server_names: list[str]
) -> None:
    """Atomically remove MCP server entries for multiple projects."""

    def _mutate(cfg: dict[str, Any]) -> None:
        projects = cfg.get("projects", {})
        for project_path in project_paths:
            project = projects.get(project_path, {})
            servers = project.get("mcpServers", {})
            for name in server_names:
                servers.pop(name, None)

    _locked_update_claude_json(_mutate)


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

            # 3. Create sentinel task for hub completion detection
            task_store = FileTaskStore(team_name)
            sentinel = task_store.create(
                subject=SENTINEL_SUBJECT,
                description="Hub must mark this completed after producing final output.",
                owner=hub_role,
            )
            sentinel_id = sentinel.id
            log.info("Sentinel task created: %s", sentinel_id)

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
            from .common import extract_input_text

            input_text = extract_input_text(initial_input)

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
                    prompt = build_clawteam_spoke_prompt(agent_cfg, hub_role)

                sys_prompt = agent_cfg.system_prompt or agent_cfg.description or ""

                cwd = str(worktrees[agent_cfg.role])
                spawn_env = self._build_spawn_env(agent_cfg.role, cwd)
                result_msg = backend.spawn(
                    command=list(self._profile.spawn_command),
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
        """Write MCP server config per harness strategy."""
        profile = self._profile

        if profile.name == "claude":
            # Claude Code: atomic write to ~/.claude.json (global config)
            mcp_url = f"http://127.0.0.1:{mcp_port}/sse"
            registrations: list[tuple[str, dict[str, dict[str, Any]]]] = []
            for wt_path in worktrees.values():
                registrations.append((str(wt_path), {
                    "masp_tools": {"type": "sse", "url": mcp_url},
                    "clawteam": {"type": "stdio", "command": clawteam_mcp_bin},
                }))
            _register_mcp_batch(registrations)

        elif profile.name == "codex":
            # Codex: write MCP servers into ~/.codex/config.toml (global config).
            # Codex uses streamable-http transport (url-based MCP config).
            # Verified working in run 863f3867 with namespace "mcp__masp_tools__".
            # Format: [mcp_servers.xxx] (from `codex mcp add` probe)
            mcp_url = f"http://127.0.0.1:{mcp_port}/mcp"
            codex_global = Path.home() / ".codex" / "config.toml"
            codex_global.parent.mkdir(parents=True, exist_ok=True)
            existing_toml = ""
            if codex_global.exists():
                existing_toml = codex_global.read_text()
            if "mcp_servers.masp_tools" not in existing_toml:
                mcp_block = (
                    f'\n[mcp_servers.masp_tools]\n'
                    f'url = "{mcp_url}"\n'
                    f'default_tools_approval_mode = "auto"\n\n'
                    f'[mcp_servers.clawteam]\n'
                    f'command = "{clawteam_mcp_bin}"\n'
                    f'default_tools_approval_mode = "auto"\n'
                )
                with open(codex_global, "a") as f:
                    f.write(mcp_block)
                log.info("Appended Codex MCP config (stdio proxy) to %s", codex_global)
            else:
                log.info("Codex MCP config already present in %s", codex_global)

        elif profile.name == "openclaw":
            # OpenClaw reads MCP config from ~/.openclaw/openclaw.json (global),
            # NOT from project-level cwd. Merge our servers into the global config.
            mcp_url = f"http://127.0.0.1:{mcp_port}/sse"
            oc_config_path = Path.home() / ".openclaw" / "openclaw.json"
            oc_config_path.parent.mkdir(parents=True, exist_ok=True)
            existing: dict[str, Any] = {}
            if oc_config_path.exists():
                try:
                    existing = json.loads(oc_config_path.read_text())
                except (json.JSONDecodeError, OSError):
                    pass
            mcp_section = existing.setdefault("mcp", {})
            servers = mcp_section.setdefault("servers", {})
            servers["masp_tools"] = {
                "url": mcp_url,
                "transport": "sse",
            }
            servers["clawteam"] = {
                "command": clawteam_mcp_bin,
            }
            oc_config_path.write_text(json.dumps(existing, indent=2))
            log.debug("Wrote OpenClaw MCP config at %s", oc_config_path)
        else:
            raise ValueError(f"Unknown harness: {profile.name}")

    def _unregister_mcp_for_harness(self, worktrees: dict[str, Path]) -> None:
        """Clean up MCP config per harness strategy."""
        profile = self._profile

        if profile.name == "claude":
            _unregister_mcp_batch(
                [str(p) for p in worktrees.values()],
                list(_MCP_SERVER_NAMES),
            )
        elif profile.name == "codex":
            # Remove our MCP servers from ~/.codex/config.toml
            codex_global = Path.home() / ".codex" / "config.toml"
            try:
                if codex_global.exists():
                    content = codex_global.read_text()
                    # Remove our appended MCP server blocks
                    for marker in ("[mcp_servers.masp_tools]", "[mcp_servers.clawteam]"):
                        idx = content.find(marker)
                        if idx >= 0:
                            # Find next section or EOF
                            next_section = content.find("\n[", idx + len(marker))
                            if next_section >= 0:
                                content = content[:idx] + content[next_section:]
                            else:
                                content = content[:idx]
                    codex_global.write_text(content.rstrip() + "\n")
            except Exception:
                pass
        elif profile.name == "openclaw":
            # Remove our MCP servers from ~/.openclaw/openclaw.json
            oc_config_path = Path.home() / ".openclaw" / "openclaw.json"
            try:
                if oc_config_path.exists():
                    cfg = json.loads(oc_config_path.read_text())
                    servers = cfg.get("mcp", {}).get("servers", {})
                    for name in _MCP_SERVER_NAMES:
                        servers.pop(name, None)
                    oc_config_path.write_text(json.dumps(cfg, indent=2))
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Per-harness spawn environment
    # ------------------------------------------------------------------

    def _build_spawn_env(self, agent_role: str, cwd: str) -> dict[str, str]:
        """Build extra environment variables for agent spawn."""
        env: dict[str, str] = {}
        if self._profile.name == "openclaw":
            # OpenClaw has a built-in bootstrap flow triggered by BOOTSTRAP.md
            # in its workspace (~/.openclaw/workspace/). Remove it so agents
            # go straight to the MASP task instead of asking "Who am I?".
            bootstrap = Path.home() / ".openclaw" / "workspace" / "BOOTSTRAP.md"
            if bootstrap.exists():
                self._openclaw_bootstrap_backup = bootstrap.read_text()
                bootstrap.unlink()
                log.debug("Removed OpenClaw BOOTSTRAP.md to skip bootstrap flow")
        elif self._profile.name == "codex":
            # Do NOT set CODEX_HOME — Codex recreates $CODEX_HOME on startup
            # and only reads config from $CODEX_HOME/config.toml, ignoring
            # the global ~/.codex/config.toml when CODEX_HOME is overridden.
            # Let all agents share ~/.codex/ so they pick up our MCP config.
            # Sessions are distinguished by thread_id in the rollout filename.
            pass
        return env

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
        """Dispatch to per-harness trace ingestor."""
        if self._profile.name == "claude":
            from .jsonl_collector import collect_all_agent_traces
            return collect_all_agent_traces(
                worktree_map=worktrees,
                catalog_tool_names=catalog_names,
                all_agent_roles=all_roles,
                action_sink=action_sink,
                hub_role=hub_role,
                spawn_timestamp=spawn_timestamp,
            )
        elif self._profile.name == "codex":
            from .codex_ingestor import collect_codex_traces
            return collect_codex_traces(
                worktree_map=worktrees,
                catalog_tool_names=catalog_names,
                all_agent_roles=all_roles,
                action_sink=action_sink,
                hub_role=hub_role,
                spawn_timestamp=spawn_timestamp,
            )
        elif self._profile.name == "openclaw":
            from .openclaw_ingestor import collect_openclaw_traces
            return collect_openclaw_traces(
                worktree_map=worktrees,
                catalog_tool_names=catalog_names,
                all_agent_roles=all_roles,
                action_sink=action_sink,
                hub_role=hub_role,
                run_id=self._current_run_id,
                spawn_timestamp=spawn_timestamp,
            )
        else:
            log.warning("No trace ingestor for harness %s — skipping trace collection",
                        self._profile.name)
            return ""

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def _cleanup_team(self, team_name: str) -> None:
        try:
            from clawteam.team.manager import TeamManager
            TeamManager.cleanup(team_name)
        except Exception:
            log.debug("ClawTeam cleanup failed for team %s", team_name, exc_info=True)

        # Restore OpenClaw BOOTSTRAP.md if we removed it
        if hasattr(self, "_openclaw_bootstrap_backup"):
            bootstrap = Path.home() / ".openclaw" / "workspace" / "BOOTSTRAP.md"
            try:
                bootstrap.write_text(self._openclaw_bootstrap_backup)
                log.debug("Restored OpenClaw BOOTSTRAP.md")
            except Exception:
                pass
