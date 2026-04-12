"""Per-run workspace isolation for SDE real fixtures.

Each benchmark run that targets a real micro-app fixture (app-ecommerce,
app-admin) gets a private, disposable copy of the fixture directory.
After the run the workspace is either destroyed (success) or kept for
inspection (failure / debug mode).

Two strategies are supported:

  * **worktree** (preferred) — ``git worktree add --detach`` from the fixture
    repo.  Near-instant, CoW-friendly, and ``git diff HEAD`` in the kept
    workspace shows exactly what the agent changed.
  * **copy** (fallback) — ``shutil.copytree`` when the fixture is not a git
    repo (e.g. during early development before ``git init``).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Patterns that should never be copied / are ephemeral
_IGNORE_PATTERNS = shutil.ignore_patterns(
    "__pycache__", "*.pyc", ".pytest_cache", ".venv", "*.egg-info",
)


@dataclass
class Workspace:
    """Handle for one run's isolated fixture workspace."""

    path: Path
    fixture_name: str
    run_id: str
    mode: str  # "worktree" | "copy"

    def diff(self) -> str:
        """Return ``git diff HEAD`` for the workspace (worktree mode only)."""
        if self.mode != "worktree":
            return ""
        try:
            res = subprocess.run(
                ["git", "diff", "HEAD"],
                cwd=self.path,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return res.stdout
        except Exception:
            return ""


@dataclass
class WorkspaceManager:
    """Create and destroy per-run isolated copies of SDE fixtures.

    Parameters
    ----------
    fixtures_root : Path
        Directory containing fixture subdirectories (``app-ecommerce/``, etc.).
    workspaces_root : Path
        Ephemeral directory where per-run workspaces are materialised.
    strategy : str
        ``"worktree"`` (default, preferred) or ``"copy"`` (fallback).
    """

    fixtures_root: Path
    workspaces_root: Path
    strategy: str = "worktree"

    # Track live workspaces so we can clean up on crash
    _live: dict[str, Workspace] = field(default_factory=dict)

    def create(self, fixture_name: str, run_id: str) -> Workspace:
        """Materialise an isolated workspace for *fixture_name*."""
        src = self.fixtures_root / fixture_name
        if not src.is_dir():
            raise FileNotFoundError(f"Fixture directory not found: {src}")

        dst = self.workspaces_root / run_id / fixture_name
        dst.parent.mkdir(parents=True, exist_ok=True)

        if self.strategy == "worktree" and (src / ".git").exists():
            ws = self._create_worktree(src, dst, fixture_name, run_id)
        else:
            ws = self._create_copy(src, dst, fixture_name, run_id)

        self._live[run_id] = ws
        log.info("workspace created: %s (%s)", ws.path, ws.mode)
        return ws

    def destroy(self, ws: Workspace, *, keep: bool = False) -> None:
        """Remove workspace.  If *keep* is True, leave it for inspection."""
        self._live.pop(ws.run_id, None)
        if keep:
            log.info("workspace kept for inspection: %s", ws.path)
            return

        if ws.mode == "worktree":
            src = self.fixtures_root / ws.fixture_name
            subprocess.run(
                ["git", "-C", str(src), "worktree", "remove", "--force", str(ws.path)],
                capture_output=True,
                timeout=30,
            )
        else:
            shutil.rmtree(ws.path, ignore_errors=True)

        # Clean up empty parent run directory
        run_dir = ws.path.parent
        if run_dir.exists() and not any(run_dir.iterdir()):
            run_dir.rmdir()

        log.info("workspace destroyed: %s", ws.path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _create_worktree(
        src: Path, dst: Path, fixture_name: str, run_id: str,
    ) -> Workspace:
        subprocess.run(
            ["git", "-C", str(src), "worktree", "add", "--detach", str(dst), "HEAD"],
            check=True,
            capture_output=True,
            timeout=30,
        )
        return Workspace(path=dst, fixture_name=fixture_name, run_id=run_id, mode="worktree")

    @staticmethod
    def _create_copy(
        src: Path, dst: Path, fixture_name: str, run_id: str,
    ) -> Workspace:
        shutil.copytree(src, dst, ignore=_IGNORE_PATTERNS, dirs_exist_ok=False)
        return Workspace(path=dst, fixture_name=fixture_name, run_id=run_id, mode="copy")
