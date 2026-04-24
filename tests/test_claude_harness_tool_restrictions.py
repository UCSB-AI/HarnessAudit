from masp_bench.frameworks.harness_runtime import (
    ClaudeHarnessRuntime,
    CodexHarnessRuntime,
    OpenClawHarnessRuntime,
)


def test_claude_spawn_command_disallows_agent_tool() -> None:
    command = ["claude", "--model", "claude-sonnet-4-6"]

    restricted = ClaudeHarnessRuntime().adjust_spawn_command(command)

    assert restricted == [
        "claude",
        "--model",
        "claude-sonnet-4-6",
        "--disallowedTools",
        "Agent",
    ]
    assert command == ["claude", "--model", "claude-sonnet-4-6"]


def test_claude_spawn_command_agent_restriction_is_idempotent() -> None:
    command = ["claude-code", "--disallowedTools", "Agent"]

    assert ClaudeHarnessRuntime().adjust_spawn_command(command) == command


def test_claude_spawn_command_merges_existing_disallowed_tools() -> None:
    command = ["claude", "--disallowedTools", "Bash"]

    assert ClaudeHarnessRuntime().adjust_spawn_command(command) == [
        "claude",
        "--disallowedTools",
        "Bash,Agent",
    ]


def test_non_claude_spawn_commands_are_not_changed() -> None:
    assert CodexHarnessRuntime().adjust_spawn_command(["codex", "--model", "gpt-5.4"]) == [
        "codex",
        "--model",
        "gpt-5.4",
    ]
    assert OpenClawHarnessRuntime().adjust_spawn_command(["openclaw", "agent"]) == [
        "openclaw",
        "agent",
    ]
