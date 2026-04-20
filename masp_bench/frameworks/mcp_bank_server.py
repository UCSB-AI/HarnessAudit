"""MCP server wrapping MASP_Bench Bank + ToolCatalog for ClawTeam agents.

Exposes every tool in the catalog as an MCP tool backed by Bank dispatch,
with fixture/mock fallback for domains without a Bank.  Runs as a standalone
SSE HTTP server so multiple concurrent Claude Code agents share one Bank
instance (consistent state).

Usage (standalone):
    python -m masp_bench.frameworks.mcp_bank_server \\
        --domain finance --port 0 --bank-dump-path /tmp/bank.db

The assigned port is printed to stdout as ``PORT=<n>`` on the first line.
On SIGTERM / atexit the Bank SQLite is dumped to ``--bank-dump-path`` so the
runner can load the mutated state for success checks.
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import json
import logging
import signal
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bank dump helper
# ---------------------------------------------------------------------------

def _dump_bank(bank: Any, dump_path: Path) -> None:
    """Backup in-memory Bank SQLite to a file for the runner to read."""
    if bank is None or not hasattr(bank, "conn"):
        return
    try:
        import sqlite3

        dump_path.parent.mkdir(parents=True, exist_ok=True)
        file_db = sqlite3.connect(str(dump_path))
        bank.conn.backup(file_db)
        file_db.close()
        log.info("Bank dumped to %s", dump_path)
    except Exception:
        log.warning("Failed to dump bank to %s", dump_path, exc_info=True)


# ---------------------------------------------------------------------------
# Server builder
# ---------------------------------------------------------------------------

def build_mcp_server(
    domain: str,
    catalog: Any,
    bank: Any = None,
    task_metadata: dict[str, Any] | None = None,
    workspace_path: Path | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> FastMCP:
    """Build a FastMCP server exposing all catalog tools backed by Bank dispatch.

    The dispatch cascade mirrors ``ToolRuntime.invoke`` (minus defense hooks,
    which are applied post-hoc during JSONL trace analysis):

      1. bank.dispatch        -- stateful backend (SQLite)
      2. SDE real tool        -- only for ``backend_type="real"`` + workspace
      3. fixture lookup       -- JSON fixture data keyed by fixture_key
      4. mock fallback        -- static ``mock_response`` string

    .. note::

        MCP does not carry caller identity, so ``bank.set_current_agent()``
        is NOT called here.  Audit trails that depend on current_agent will
        show empty/default values.  This is a known limitation documented in
        the plan.
    """
    mcp = FastMCP("masp_tools", host=host, port=port)

    if bank is not None and task_metadata and hasattr(bank, "apply_task_metadata"):
        bank.apply_task_metadata(task_metadata)

    # Shared lock prevents concurrent Bank mutations when FastMCP dispatches
    # sync handlers via its internal thread pool.
    _bank_lock = asyncio.Lock()

    for tool_def in catalog.tools:

        def _make_handler(td: Any) -> Any:
            async def handler(**kwargs: str) -> str:
                # --- 1. Bank dispatch ---
                if bank is not None:
                    async with _bank_lock:
                        try:
                            return str(bank.dispatch(td.name, kwargs))
                        except Exception as exc:
                            return f"Error dispatching {td.name}: {exc}"

                # --- 2. SDE real tool ---
                if td.backend_type == "real" and workspace_path is not None:
                    from masp_bench.sde_backend import dispatch_real_tool

                    return str(dispatch_real_tool(td.name, kwargs, workspace_path))

                # --- 3. Fixture lookup ---
                fixture_data = td.fixture_data
                fixture_key = td.fixture_key
                if fixture_data and fixture_key:
                    lookup_val = str(kwargs.get(fixture_key, ""))
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
                    return "\n".join(lines)

                # --- 4. Mock fallback ---
                return td.mock_response or f"{td.name}: OK"

            handler.__name__ = td.name
            handler.__doc__ = td.description or td.name
            handler.__annotations__ = {p: str for p in td.params}
            handler.__annotations__["return"] = str
            return handler

        mcp.tool()(_make_handler(tool_def))

    return mcp


# ---------------------------------------------------------------------------
# Standalone entry-point
# ---------------------------------------------------------------------------

def _run_server() -> None:
    """Parse CLI args, build server, and run with SSE transport."""
    parser = argparse.ArgumentParser(description="MASP-Bench domain MCP server")
    parser.add_argument("--domain", required=True)
    parser.add_argument("--port", type=int, default=0, help="SSE port (0 = OS-assigned)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--task-metadata-json", default=None)
    parser.add_argument("--bank-dump-path", default=None,
                        help="Path to dump Bank SQLite on exit")
    args = parser.parse_args()

    from masp_bench.banks import get_bank
    from masp_bench.loader import load_tool_catalog

    catalog = load_tool_catalog(args.domain)
    bank = get_bank(args.domain)

    task_metadata: dict[str, Any] | None = None
    if args.task_metadata_json:
        try:
            task_metadata = json.loads(args.task_metadata_json)
        except json.JSONDecodeError:
            log.warning("Failed to parse --task-metadata-json, ignoring")

    # Register Bank dump on exit (SIGTERM / atexit).
    dump_path = Path(args.bank_dump_path) if args.bank_dump_path else None
    if dump_path and bank is not None:
        def _on_exit(*_args: Any) -> None:
            _dump_bank(bank, dump_path)

        atexit.register(_on_exit)
        signal.signal(signal.SIGTERM, lambda *a: (atexit._run_exitfuncs(), sys.exit(0)))

    # Resolve port before building the server.
    port = args.port
    if port == 0:
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((args.host, 0))
            port = s.getsockname()[1]

    mcp = build_mcp_server(
        args.domain, catalog, bank=bank, task_metadata=task_metadata,
        host=args.host, port=port,
    )

    print(f"PORT={port}", flush=True)
    sys.stdout.flush()

    mcp.run(transport="sse")


if __name__ == "__main__":
    _run_server()
