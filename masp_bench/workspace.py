"""Per-run workspace isolation for SDE real fixtures.

Each benchmark run that targets a real micro-app fixture (app-ecommerce,
app-admin) gets a private, disposable git worktree of the fixture directory.
After the run the workspace is either destroyed (success) or kept for
inspection (failure / debug mode).

``git worktree add --detach`` is near-instant, CoW-friendly, and
``git diff HEAD`` in the kept workspace shows exactly what the agent changed.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Git worktree I/O can be slow on shared filesystems; avoid flaky timeouts.
_WORKTREE_TIMEOUT_S = 120


@dataclass
class Workspace:
    """Handle for one run's isolated fixture workspace."""

    path: Path
    fixture_name: str
    run_id: str

    def diff(self) -> str:
        """Return ``git diff HEAD`` for the workspace."""
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
    """Create and destroy per-run git worktrees of SDE fixtures.

    Parameters
    ----------
    fixtures_root : Path
        Directory containing fixture subdirectories (``app-ecommerce/``, etc.).
        Each fixture **must** be a standalone git repo (``git init`` + at least
        one commit).
    workspaces_root : Path
        Ephemeral directory where per-run worktrees are materialised.
    """

    fixtures_root: Path
    workspaces_root: Path

    # Track live workspaces so we can clean up on crash
    _live: dict[str, Workspace] = field(default_factory=dict)

    def create(self, fixture_name: str, run_id: str) -> Workspace:
        """Create an isolated git worktree for *fixture_name*."""
        src = self.fixtures_root / fixture_name
        if not src.is_dir():
            raise FileNotFoundError(f"Fixture directory not found: {src}")
        if not (src / ".git").exists():
            raise RuntimeError(
                f"Fixture {src} is not a git repo. "
                f"Run 'git init && git add -A && git commit -m init' inside it."
            )

        dst = self.workspaces_root / run_id / fixture_name
        dst.parent.mkdir(parents=True, exist_ok=True)

        # Always prune stale worktree entries first.  Git tracks worktrees in
        # .git/worktrees/ independently of the filesystem — a previous crash
        # may have left the directory partially removed while git still thinks
        # the worktree exists, causing `worktree add` to fail with exit 128.
        subprocess.run(
            ["git", "-C", str(src), "worktree", "prune"],
            capture_output=True, timeout=_WORKTREE_TIMEOUT_S,
        )
        # If the directory itself still exists, force-remove it
        if dst.exists():
            log.warning("stale workspace found at %s — removing", dst)
            subprocess.run(
                ["git", "-C", str(src), "worktree", "remove", "--force", str(dst)],
                capture_output=True, timeout=_WORKTREE_TIMEOUT_S,
            )
            subprocess.run(
                ["git", "-C", str(src), "worktree", "prune"],
                capture_output=True, timeout=_WORKTREE_TIMEOUT_S,
            )

        subprocess.run(
            ["git", "-C", str(src), "worktree", "add", "--detach", str(dst), "HEAD"],
            check=True,
            capture_output=True,
            timeout=_WORKTREE_TIMEOUT_S,
        )

        ws = Workspace(path=dst, fixture_name=fixture_name, run_id=run_id)
        self._live[run_id] = ws
        log.info("workspace created: %s", ws.path)
        return ws

    def destroy(self, ws: Workspace, *, keep: bool = False) -> None:
        """Remove workspace.  If *keep* is True, leave it for inspection."""
        self._live.pop(ws.run_id, None)
        if keep:
            log.info("workspace kept for inspection: %s", ws.path)
            return

        src = self.fixtures_root / ws.fixture_name
        subprocess.run(
            ["git", "-C", str(src), "worktree", "remove", "--force", str(ws.path)],
            capture_output=True,
            timeout=_WORKTREE_TIMEOUT_S,
        )

        # Clean up empty parent run directory
        run_dir = ws.path.parent
        if run_dir.exists() and not any(run_dir.iterdir()):
            run_dir.rmdir()

        log.info("workspace destroyed: %s", ws.path)
