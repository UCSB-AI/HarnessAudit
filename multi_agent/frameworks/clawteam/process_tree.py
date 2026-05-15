"""Process discovery and termination helpers for ClawTeam adapter execution."""

from __future__ import annotations

import logging
import os
import shutil
import signal
import sys
import time
from pathlib import Path

log = logging.getLogger(__name__)


def _tail_text(path: Path, *, limit: int = 4000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(text) <= limit:
        return text.strip()
    return text[-limit:].strip()


def _resolve_current_env_executable(name: str) -> str:
    resolved = shutil.which(name)
    if resolved:
        return resolved
    candidate = Path(sys.executable).resolve().parent / name
    if candidate.is_file():
        return str(candidate)
    return name



def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _proc_stat_ppid(pid: int) -> int | None:
    try:
        stat = Path("/proc") / str(pid) / "stat"
        text = stat.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    close = text.rfind(")")
    if close < 0:
        return None
    fields = text[close + 2:].split()
    if len(fields) < 2:
        return None
    try:
        return int(fields[1])
    except ValueError:
        return None


def _descendant_pids(root_pid: int) -> set[int]:
    children: dict[int, list[int]] = {}
    try:
        proc_entries = list(Path("/proc").iterdir())
    except OSError:
        return set()

    for entry in proc_entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        ppid = _proc_stat_ppid(pid)
        if ppid is None:
            continue
        children.setdefault(ppid, []).append(pid)

    descendants: set[int] = set()
    stack = list(children.get(root_pid, []))
    while stack:
        pid = stack.pop()
        if pid in descendants:
            continue
        descendants.add(pid)
        stack.extend(children.get(pid, []))
    return descendants


def _team_process_pids(team_name: str) -> set[int]:
    needle = f"CLAWTEAM_TEAM_NAME={team_name}".encode()
    pids: set[int] = set()
    try:
        proc_entries = list(Path("/proc").iterdir())
    except OSError:
        return pids

    for entry in proc_entries:
        if not entry.name.isdigit():
            continue
        try:
            environ = (entry / "environ").read_bytes()
        except OSError:
            continue
        if needle in environ.split(b"\0"):
            pids.add(int(entry.name))
    return pids


def _signal_pids(pids: set[int], sig: signal.Signals) -> None:
    current_pid = os.getpid()
    for pid in sorted(pids, reverse=True):
        if pid == current_pid or pid <= 0:
            continue
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass
        except PermissionError:
            log.warning("Permission denied sending %s to pid %s", sig.name, pid)


def _kill_pid_family(pid: int, *, grace_seconds: float = 1.0) -> None:
    if not _pid_exists(pid):
        return

    current_pgid = os.getpgrp()
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        pgid = None
    except PermissionError:
        pgid = None

    if pgid and pgid > 0 and pgid != current_pgid:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            log.warning("Permission denied terminating process group %s", pgid)

    pids = _descendant_pids(pid)
    pids.add(pid)
    _signal_pids(pids, signal.SIGTERM)

    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if not any(_pid_exists(p) for p in pids):
            return
        time.sleep(0.05)

    remaining = {p for p in pids if _pid_exists(p)}
    if remaining:
        _signal_pids(remaining, signal.SIGKILL)
