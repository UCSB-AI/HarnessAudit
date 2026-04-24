from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from .base import MASFrameworkAdapter, RunContext, RunOutcome
from .common import extract_input_text, restore_bank_from_dump, stop_mcp_server
from .openclaw_local_ingestor import ingest_openclaw_local_session
from .openclaw_local_runtime import OpenClawLocalRuntime


class OpenClawLocalAdapter(MASFrameworkAdapter):
    framework_id = "openclaw_local"

    def __init__(self, agent_timeout: int = 300, harness: str = "openclaw") -> None:
        self._agent_timeout = agent_timeout
        self._harness = harness

    async def run(
        self,
        ctx: RunContext,
        initial_input: str | list[dict[str, Any]],
    ) -> RunOutcome:
        if len(ctx.task.agents) != 1:
            return RunOutcome(
                framework=self.framework_id,
                final_output="",
                error=(
                    "openclaw_local only supports single-agent tasks; "
                    f"got {len(ctx.task.agents)} agents."
                ),
            )

        runtime = OpenClawLocalRuntime()
        bank_dump_path = Path(f"/tmp/masp_bank_{ctx.run_id}.db")
        mcp_proc: subprocess.Popen | None = None
        final_output = ""

        try:
            mcp_proc, mcp_port = await self._start_mcp_server(ctx, bank_dump_path)
            workspace = getattr(ctx.workspace, "path", None)
            session = runtime.prepare_session(
                run_id=ctx.run_id,
                model=ctx.model,
                workspace=workspace,
                task_domain=ctx.task.domain,
                mcp_url=f"http://127.0.0.1:{mcp_port}/sse",
                timeout_seconds=self._agent_timeout,
            )

            prompt = extract_input_text(initial_input) or ctx.task.goal

            import time as _time

            spawn_timestamp = _time.time()
            await asyncio.to_thread(self._run_openclaw_agent, session, prompt)

            stop_mcp_server(mcp_proc)
            mcp_proc = None
            restore_bank_from_dump(ctx.bank, bank_dump_path)

            result = ingest_openclaw_local_session(
                session.session_path,
                created_after=spawn_timestamp,
                agent_role=ctx.task.agents[0].role,
                catalog_tool_names={tool.name for tool in ctx.catalog.tools},
                action_sink=ctx.action_sink,
            )
            final_output = result.final_output
            if result.error:
                return RunOutcome(
                    framework=self.framework_id,
                    final_output=final_output,
                    error=result.error,
                )

            ctx.action_sink.emit_communication(
                agent_id=ctx.task.agents[0].role,
                agent_role=ctx.task.agents[0].role,
                target_agent="user",
                target_role="user",
                content=final_output,
                raw_event={
                    "framework": self.framework_id,
                    "communication_kind": "user_output",
                    "capture_mode": "openclaw_local_session_final_text",
                },
            )

            return RunOutcome(framework=self.framework_id, final_output=final_output)

        except Exception as exc:
            return RunOutcome(
                framework=self.framework_id,
                final_output=final_output,
                error=str(exc),
            )
        finally:
            stop_mcp_server(mcp_proc)
            runtime.cleanup()
            try:
                bank_dump_path.unlink(missing_ok=True)
            except Exception:
                pass

    def _run_openclaw_agent(self, session: Any, prompt: str) -> None:
        completed = subprocess.run(
            [*session.command, "--message", prompt],
            cwd=str(session.workspace),
            env=session.env,
            capture_output=True,
            text=True,
            timeout=self._agent_timeout + 30,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(
                f"openclaw agent failed with exit code {completed.returncode}: {detail}"
            )

    async def _start_mcp_server(
        self,
        ctx: RunContext,
        bank_dump_path: Path,
    ) -> tuple[subprocess.Popen[str], int]:
        cmd = self._build_mcp_server_cmd(ctx, bank_dump_path)
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        loop = asyncio.get_running_loop()
        stdout = proc.stdout
        if stdout is None:
            proc.kill()
            raise RuntimeError("MCP server subprocess has no stdout pipe")
        try:
            line = await asyncio.wait_for(
                loop.run_in_executor(None, stdout.readline),
                timeout=30,
            )
        except asyncio.TimeoutError as exc:
            proc.kill()
            raise RuntimeError(f"Failed to read MCP server port: {exc}") from exc

        if not line or not line.startswith("PORT="):
            proc.kill()
            raise RuntimeError("MCP server did not report a port")

        port = int(line.strip().split("=", 1)[1])
        await asyncio.sleep(0.5)
        return proc, port

    def _build_mcp_server_cmd(
        self,
        ctx: RunContext,
        bank_dump_path: Path,
    ) -> list[str]:
        task_metadata_json = json.dumps(ctx.task.metadata) if ctx.task.metadata else "{}"
        return [
            sys.executable,
            "-m",
            "masp_bench.frameworks.mcp_bank_server",
            "--domain",
            ctx.task.domain,
            "--port",
            "0",
            "--transport",
            "sse",
            "--task-metadata-json",
            task_metadata_json,
            "--bank-dump-path",
            str(bank_dump_path),
            "--bank-source",
            ctx.bank_source,
        ]

