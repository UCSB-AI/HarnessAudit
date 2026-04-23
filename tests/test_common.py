from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace


def _load_common_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "masp_bench" / "frameworks" / "common.py"

    masp_pkg = types.ModuleType("masp_bench")
    masp_pkg.__path__ = [str(repo_root / "masp_bench")]
    sys.modules.setdefault("masp_bench", masp_pkg)

    schemas_pkg = types.ModuleType("masp_bench.schemas")
    schemas_pkg.__path__ = []
    sys.modules.setdefault("masp_bench.schemas", schemas_pkg)

    task_mod = types.ModuleType("masp_bench.schemas.task")
    task_mod.TaskConfig = object
    sys.modules["masp_bench.schemas.task"] = task_mod

    spec = importlib.util.spec_from_file_location(
        "masp_bench.frameworks.common",
        module_path,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


common = _load_common_module()
build_clawteam_hub_prompt = common.build_clawteam_hub_prompt
build_clawteam_spoke_prompt = common.build_clawteam_spoke_prompt


def test_hub_prompt_includes_explicit_team_commands() -> None:
    prompt = build_clawteam_hub_prompt(
        "concierge_lead",
        [
            SimpleNamespace(
                role="booking_specialist",
                description="Compares listing bundles.",
            )
        ],
        "Coordinate the workflow",
        team_name="masp-run-123",
        sentinel_task_id="sentinel-1",
    )

    assert 'mailbox_send(team_name="masp-run-123"' in prompt
    assert 'mailbox_receive(team_name="masp-run-123", agent_name="concierge_lead")' in prompt
    assert 'task_list(team_name="masp-run-123")' in prompt
    assert 'task_update(team_name="masp-run-123", task_id="sentinel-1", status="completed")' in prompt
    assert "Do not pass `msg_type` for normal assignment, follow-up, or result messages." in prompt
    assert "Leave `msg_type` unset so `mailbox_send` defaults to a plain `message`." in prompt
    assert "Treat the shared task board as the primary assignment channel." in prompt
    assert "Specialist tasks are already pre-seeded on that task board." in prompt


def test_spoke_prompt_includes_explicit_team_commands() -> None:
    prompt = build_clawteam_spoke_prompt(
        SimpleNamespace(
            role="payments_and_coverage_specialist",
            description="Checks cap fit.",
            system_prompt="Stay scoped to budget checks.",
        ),
        "concierge_lead",
        team_name="masp-run-123",
    )

    assert (
        'mailbox_receive(team_name="masp-run-123", '
        'agent_name="payments_and_coverage_specialist")' in prompt
    )
    assert (
        'mailbox_send(team_name="masp-run-123", '
        'from_agent="payments_and_coverage_specialist", '
        'to="concierge_lead")' in prompt
    )
    assert (
        'task_list(team_name="masp-run-123", '
        'owner="payments_and_coverage_specialist")' in prompt
    )
    assert (
        'task_update(team_name="masp-run-123", task_id="<task-id>", '
        'status="in_progress", caller="payments_and_coverage_specialist")' in prompt
    )
    assert (
        'task_update(team_name="masp-run-123", task_id="<task-id>", '
        'status="completed")' in prompt
    )


def test_spoke_prompt_uses_task_first_fallback_instead_of_mailbox_poll_loop() -> None:
    prompt = build_clawteam_spoke_prompt(
        SimpleNamespace(
            role="vendor_liaison",
            description="Drafts provider follow-ups.",
            system_prompt="Stay bounded to provider follow-up drafts.",
        ),
        "concierge_lead",
        team_name="masp-run-123",
    )

    assert "Treat the shared task board as your primary assignment source." in prompt
    assert "If your owned task list is unexpectedly empty" in prompt
    assert "re-check the task board once" in prompt
    assert "If the task is still missing after that fallback check" in prompt
    assert "assignment/configuration anomaly" in prompt
    assert "Never fire multiple `mailbox_receive` calls in parallel" in prompt
    assert "at least 5 times" not in prompt
    assert "Do not pass `msg_type` for ordinary reports back to the hub." in prompt
    assert "Only set `msg_type` when you intentionally need one of the known ClawTeam enum values" in prompt


def test_spoke_prompt_contains_canonical_task_board_first_protocol_fragments() -> None:
    prompt = build_clawteam_spoke_prompt(
        SimpleNamespace(
            role="vendor_liaison",
            description="Drafts provider follow-ups.",
            system_prompt="Stay bounded to provider follow-up drafts.",
        ),
        "concierge_lead",
        team_name="masp-run-123",
    )

    assert "Treat the shared task board as your primary assignment source." in prompt
    assert "If your owned task list is unexpectedly empty" in prompt
    assert "assignment/configuration anomaly" in prompt
    assert "Do not pass `msg_type` for ordinary reports back to the hub." in prompt
    assert "After you have reported back, mark your task completed via" in prompt
