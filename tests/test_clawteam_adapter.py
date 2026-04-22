from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace


def _load_clawteam_adapter_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "masp_bench" / "frameworks" / "clawteam_adapter.py"

    masp_pkg = types.ModuleType("masp_bench")
    masp_pkg.__path__ = [str(repo_root / "masp_bench")]
    sys.modules.setdefault("masp_bench", masp_pkg)

    frameworks_pkg = types.ModuleType("masp_bench.frameworks")
    frameworks_pkg.__path__ = [str(repo_root / "masp_bench" / "frameworks")]
    sys.modules.setdefault("masp_bench.frameworks", frameworks_pkg)

    base_mod = types.ModuleType("masp_bench.frameworks.base")
    base_mod.MASFrameworkAdapter = object
    base_mod.RunContext = object
    base_mod.RunOutcome = object
    sys.modules["masp_bench.frameworks.base"] = base_mod

    common_mod = types.ModuleType("masp_bench.frameworks.common")
    common_mod.build_clawteam_hub_prompt = lambda *args, **kwargs: ""
    common_mod.build_clawteam_spoke_prompt = lambda *args, **kwargs: ""
    sys.modules["masp_bench.frameworks.common"] = common_mod

    harness_mod = types.ModuleType("masp_bench.frameworks.harness_profiles")
    harness_mod.HarnessProfile = object
    harness_mod.get_harness_profile = lambda name: SimpleNamespace(
        name=name,
        spawn_command=(),
        mcp_transport="sse",
    )
    harness_mod.session_name = lambda run_id, role, harness: f"{run_id}-{role}"
    sys.modules["masp_bench.frameworks.harness_profiles"] = harness_mod

    spec = importlib.util.spec_from_file_location(
        "masp_bench.frameworks.clawteam_adapter",
        module_path,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


adapter = _load_clawteam_adapter_module()
seed_spoke_tasks = adapter.seed_spoke_tasks


class FakeTaskStore:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(id=f"task-{len(self.created)}")


def test_seed_spoke_tasks_creates_owned_task_for_each_specialist() -> None:
    store = FakeTaskStore()
    spoke_agents = [
        SimpleNamespace(
            role="booking_specialist",
            description="Compares listing bundles.",
            system_prompt="Stay scoped to shortlist comparison.",
        ),
        SimpleNamespace(
            role="vendor_liaison",
            description="Handles provider follow-up.",
            system_prompt="Stay scoped to provider outreach.",
        ),
    ]

    task_ids = seed_spoke_tasks(
        store,
        spoke_agents,
        task_goal="Pick the best moving bundle and coordinate next steps.",
        hub_role="concierge_lead",
        team_name="masp-run-123",
    )

    assert task_ids == {
        "booking_specialist": "task-1",
        "vendor_liaison": "task-2",
    }
    assert [entry["owner"] for entry in store.created] == [
        "booking_specialist",
        "vendor_liaison",
    ]
    assert store.created[0]["subject"] == "booking_specialist: Compares listing bundles."
    assert store.created[1]["subject"] == "vendor_liaison: Handles provider follow-up."

    first_description = store.created[0]["description"]
    assert "Primary assignment channel: the shared task board for team masp-run-123." in first_description
    assert "Overall task goal:" in first_description
    assert "Pick the best moving bundle and coordinate next steps." in first_description
    assert (
        'task_update(team_name="masp-run-123", task_id="<this-task-id>", '
        'status="in_progress", caller="booking_specialist")' in first_description
    )
    assert (
        'mailbox_send(team_name="masp-run-123", '
        'from_agent="booking_specialist", to="concierge_lead")' in first_description
    )

    metadata = store.created[0]["metadata"]
    assert metadata == {
        "assignment_mode": "task_board_first",
        "hub_role": "concierge_lead",
        "role": "booking_specialist",
    }
