from pathlib import Path

from masp_bench.loader import load_task


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_dl_t3_vendor_liaison_definition_matches_message_provider_checker() -> None:
    task = load_task(
        REPO_ROOT
        / "masp_bench"
        / "tasks"
        / "daily_life"
        / "housing"
        / "dl-t3.yaml"
    )

    vendor = next(agent for agent in task.agents if agent.role == "vendor_liaison")
    prompt = vendor.system_prompt.lower()
    description = vendor.description.lower()
    goal = task.goal.lower()

    assert "message_provider" in vendor.tool_necessity.required
    assert "draft only" not in prompt
    assert "may not contact" not in prompt
    assert "send" in prompt
    assert "message_provider" in prompt
    assert "send" in description
    assert "send at least one bounded provider follow-up" in goal
