"""Tests for clawteam.spawn.prompt — build_agent_prompt."""

from clawteam.spawn.prompt import build_agent_prompt


class TestBuildAgentPrompt:
    def test_basic_prompt_contains_identity(self):
        prompt = build_agent_prompt(
            agent_name="worker-1",
            agent_id="abc123",
            agent_type="coder",
            team_name="alpha",
            leader_name="leader",
            task="Implement feature X",
        )
        assert "worker-1" in prompt
        assert "abc123" in prompt
        assert "coder" in prompt
        assert "alpha" in prompt
        assert "leader" in prompt
        assert "Implement feature X" in prompt

    def test_prompt_contains_coordination_protocol(self):
        prompt = build_agent_prompt(
            agent_name="w", agent_id="id", agent_type="t",
            team_name="team", leader_name="lead", task="do stuff",
        )
        assert "clawteam task list" in prompt
        assert "If that list is empty" in prompt
        assert "clawteam task update" in prompt
        assert "commit your changes" in prompt
        assert "git add -A && git commit" in prompt
        assert "clawteam inbox send" in prompt
        assert "clawteam cost report" in prompt
        assert "clawteam session save" not in prompt

    def test_prompt_includes_user_when_provided(self):
        prompt = build_agent_prompt(
            agent_name="w", agent_id="id", agent_type="t",
            team_name="team", leader_name="lead", task="task",
            user="alice",
        )
        assert "alice" in prompt

    def test_prompt_excludes_user_when_empty(self):
        prompt = build_agent_prompt(
            agent_name="w", agent_id="id", agent_type="t",
            team_name="team", leader_name="lead", task="task",
            user="",
        )
        assert "User:" not in prompt

    def test_prompt_includes_workspace_when_provided(self):
        prompt = build_agent_prompt(
            agent_name="w", agent_id="id", agent_type="t",
            team_name="team", leader_name="lead", task="task",
            workspace_dir="/tmp/ws", workspace_branch="feature-x",
            isolated_workspace=True,
        )
        assert "/tmp/ws" in prompt
        assert "feature-x" in prompt
        assert "Workspace" in prompt
        assert "isolated git worktree" in prompt

    def test_prompt_for_plain_repo_path_is_not_described_as_worktree(self):
        prompt = build_agent_prompt(
            agent_name="w", agent_id="id", agent_type="t",
            team_name="team", leader_name="lead", task="task",
            workspace_dir="/tmp/repo",
            isolated_workspace=False,
        )
        assert "/tmp/repo" in prompt
        assert "Work directly in this repository path" in prompt
        assert "isolated git worktree" not in prompt
        assert "Branch:" not in prompt

    def test_prompt_excludes_workspace_when_empty(self):
        prompt = build_agent_prompt(
            agent_name="w", agent_id="id", agent_type="t",
            team_name="team", leader_name="lead", task="task",
            workspace_dir="",
        )
        assert "Workspace" not in prompt

    def test_prompt_uses_team_and_leader_in_commands(self):
        prompt = build_agent_prompt(
            agent_name="dev", agent_id="id", agent_type="t",
            team_name="my-team", leader_name="boss", task="task",
        )
        assert "clawteam task list my-team --owner dev" in prompt
        assert "clawteam inbox send my-team boss" in prompt
        assert "clawteam cost report my-team" in prompt
        assert "commit your changes in this repository with git" in prompt

    def test_prompt_includes_worker_loop_protocol(self):
        prompt = build_agent_prompt(
            agent_name="dev", agent_id="id", agent_type="t",
            team_name="my-team", leader_name="boss", task="task",
        )
        assert "Worker Loop Protocol" in prompt
        assert "Do not exit after the first task" in prompt
        assert "do not start a detached daemon/watch loop" in prompt
        assert "Keep the monitoring/reporting loop in the foreground" in prompt
        assert "scan `clawteam task list my-team`" in prompt
        assert "clawteam inbox receive my-team --agent dev" in prompt
        assert "clawteam lifecycle idle my-team" in prompt
