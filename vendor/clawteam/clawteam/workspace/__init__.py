"""Git worktree workspace isolation for ClawTeam agents."""

from __future__ import annotations

from pathlib import Path

from clawteam.workspace.manager import WorkspaceManager


def get_workspace_manager(repo_path: str | None = None) -> WorkspaceManager | None:
    """Return a WorkspaceManager if inside a git repo, else None."""
    path = Path(repo_path) if repo_path else None
    return WorkspaceManager.try_create(path)
