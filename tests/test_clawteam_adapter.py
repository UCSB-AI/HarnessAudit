from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace


def _load_clawteam_adapter_module(runtime=None):
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "masp_bench" / "frameworks" / "clawteam_adapter.py"

    masp_pkg = types.ModuleType("masp_bench")
    masp_pkg.__path__ = [str(repo_root / "masp_bench")]
    sys.modules.setdefault("masp_bench", masp_pkg)

    frameworks_pkg = types.ModuleType("masp_bench.frameworks")
    frameworks_pkg.__path__ = [str(repo_root / "masp_bench" / "frameworks")]
    sys.modules.setdefault("masp_bench.frameworks", frameworks_pkg)

    schemas_pkg = types.ModuleType("masp_bench.schemas")
    schemas_pkg.__path__ = []
    sys.modules.setdefault("masp_bench.schemas", schemas_pkg)

    task_mod = types.ModuleType("masp_bench.schemas.task")
    task_mod.TaskConfig = object
    sys.modules["masp_bench.schemas.task"] = task_mod

    base_mod = types.ModuleType("masp_bench.frameworks.base")
    base_mod.MASFrameworkAdapter = object
    base_mod.RunContext = object
    base_mod.RunOutcome = object
    sys.modules["masp_bench.frameworks.base"] = base_mod

    common_spec = importlib.util.spec_from_file_location(
        "masp_bench.frameworks.common",
        repo_root / "masp_bench" / "frameworks" / "common.py",
    )
    assert common_spec and common_spec.loader
    common_mod = importlib.util.module_from_spec(common_spec)
    sys.modules[common_spec.name] = common_mod
    common_spec.loader.exec_module(common_mod)

    harness_mod = types.ModuleType("masp_bench.frameworks.harness_profiles")
    harness_mod.HarnessProfile = object
    harness_mod.get_harness_profile = lambda name: SimpleNamespace(
        name=name,
        spawn_command=(),
        mcp_transport="sse",
    )
    harness_mod.session_name = lambda run_id, role, harness: f"{run_id}-{role}"
    sys.modules["masp_bench.frameworks.harness_profiles"] = harness_mod

    runtime_mod = types.ModuleType("masp_bench.frameworks.harness_runtime")
    runtime_mod.HarnessRuntime = object
    runtime_mod.get_harness_runtime = lambda name: runtime or SimpleNamespace(
        register_mcp=lambda **kwargs: None,
        unregister_mcp=lambda **kwargs: None,
        build_spawn_env=lambda **kwargs: {},
        session_id=lambda **kwargs: None,
        collect_traces=lambda **kwargs: "",
        cleanup=lambda: None,
    )
    sys.modules["masp_bench.frameworks.harness_runtime"] = runtime_mod

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
    assert "Treat the shared task board as your primary assignment source." in first_description
    assert "If your owned task list is unexpectedly empty" in first_description
    assert "assignment/configuration anomaly" in first_description
    assert "Do not pass `msg_type` for ordinary reports back to the hub." in first_description
    assert "After you have reported back, mark your task completed via" in first_description

    metadata = store.created[0]["metadata"]
    assert metadata == {
        "assignment_mode": "task_board_first",
        "hub_role": "concierge_lead",
        "role": "booking_specialist",
    }


def test_adapter_registers_mcp_via_runtime_strategy() -> None:
    recorded: list[dict[str, object]] = []

    runtime = SimpleNamespace(
        register_mcp=lambda **kwargs: recorded.append(kwargs),
        unregister_mcp=lambda **kwargs: None,
        build_spawn_env=lambda **kwargs: {},
        session_id=lambda **kwargs: None,
        collect_traces=lambda **kwargs: "",
        cleanup=lambda: None,
    )
    adapter_mod = _load_clawteam_adapter_module(runtime)
    clawteam_adapter = adapter_mod.ClawTeamAdapter(harness="codex")

    worktrees = {"concierge_lead": Path("/tmp/concierge")}
    clawteam_adapter._register_mcp_for_harness(worktrees, 8123, "clawteam-mcp")

    assert recorded == [
        {
            "worktrees": worktrees,
            "mcp_port": 8123,
            "clawteam_command": "clawteam-mcp",
        }
    ]


def test_adapter_builds_spawn_env_and_collects_traces_via_runtime_strategy() -> None:
    captured: dict[str, object] = {}

    def build_spawn_env(**kwargs):
        captured["env"] = kwargs
        return {"RUNTIME_ROLE": kwargs["agent_role"]}

    def collect_traces(**kwargs):
        captured["collect"] = kwargs
        return "final summary"

    runtime = SimpleNamespace(
        register_mcp=lambda **kwargs: None,
        unregister_mcp=lambda **kwargs: None,
        build_spawn_env=build_spawn_env,
        session_id=lambda **kwargs: "session-123",
        collect_traces=collect_traces,
        cleanup=lambda: None,
    )
    adapter_mod = _load_clawteam_adapter_module(runtime)
    clawteam_adapter = adapter_mod.ClawTeamAdapter(harness="openclaw")
    clawteam_adapter._current_run_id = "ct-openclaw-test"

    env = clawteam_adapter._build_spawn_env("vendor_liaison", "/tmp/vendor")
    result = clawteam_adapter._collect_traces(
        worktrees={"vendor_liaison": Path("/tmp/vendor")},
        catalog_names={"mailbox_send"},
        all_roles=["vendor_liaison", "concierge_lead"],
        action_sink="sink",
        hub_role="concierge_lead",
        spawn_timestamp=123.0,
    )

    assert env == {"RUNTIME_ROLE": "vendor_liaison"}
    assert captured["env"] == {
        "agent_role": "vendor_liaison",
        "cwd": "/tmp/vendor",
    }
    assert result == "final summary"
    assert captured["collect"] == {
        "run_id": "ct-openclaw-test",
        "worktrees": {"vendor_liaison": Path("/tmp/vendor")},
        "catalog_names": {"mailbox_send"},
        "all_roles": ["vendor_liaison", "concierge_lead"],
        "action_sink": "sink",
        "hub_role": "concierge_lead",
        "spawn_timestamp": 123.0,
    }
