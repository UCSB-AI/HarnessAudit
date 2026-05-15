"""Shared MCP server lifecycle helpers for framework adapters."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any


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
