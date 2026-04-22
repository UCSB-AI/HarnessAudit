from __future__ import annotations

import importlib.util
import inspect
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

from masp_bench.frameworks.harness_backbone import replace_run_id_model


def test_replace_run_id_model_swaps_requested_model_with_observed_backbone() -> None:
    assert (
        replace_run_id_model(
            "ct-codex-claude-sonnet-4-7b933142",
            "gpt-5.4",
        )
        == "ct-codex-gpt-5.4-7b933142"
    )


def test_replace_run_id_model_preserves_unexpected_formats() -> None:
    assert replace_run_id_model("custom-run-id", "gpt-5.4") == "custom-run-id"


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_checker_module():
    masp_pkg = types.ModuleType("masp_bench")
    masp_pkg.__path__ = [str(REPO_ROOT / "masp_bench")]
    sys.modules.setdefault("masp_bench", masp_pkg)

    schemas_pkg = types.ModuleType("masp_bench.schemas")
    schemas_pkg.__path__ = [str(REPO_ROOT / "masp_bench" / "schemas")]
    sys.modules["masp_bench.schemas"] = schemas_pkg

    recognizer_mod = types.ModuleType("masp_bench.recognizer")
    recognizer_mod.contains = lambda payload, domain, data_class: False
    sys.modules["masp_bench.recognizer"] = recognizer_mod

    actions_mod = types.ModuleType("masp_bench.schemas.actions")
    actions_mod.ObservableAction = object
    sys.modules["masp_bench.schemas.actions"] = actions_mod

    access_rules_mod = types.ModuleType("masp_bench.schemas.access_rules")

    class AccessDecision:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

        @classmethod
        def allowed(cls, action_seq=None):
            return cls(violated=False, action_sequence_num=action_seq)

    class AccessRule:
        pass

    class ViolationCode:
        V_OT = "V-OT"
        V_OR = "V-OR"
        V_IC = "V-IC"
        V_ID = "V-ID"

    access_rules_mod.AccessDecision = AccessDecision
    access_rules_mod.AccessRule = AccessRule
    access_rules_mod.ViolationCode = ViolationCode
    sys.modules["masp_bench.schemas.access_rules"] = access_rules_mod

    trace_mod = types.ModuleType("masp_bench.schemas.trace")

    class RunMetrics:
        pass

    trace_mod.RunMetrics = RunMetrics
    sys.modules["masp_bench.schemas.trace"] = trace_mod

    spec = importlib.util.spec_from_file_location(
        "masp_bench.checker",
        REPO_ROOT / "masp_bench" / "checker.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_cli_module(record: dict[str, object]):
    masp_pkg = types.ModuleType("masp_bench")
    masp_pkg.__path__ = [str(REPO_ROOT / "masp_bench")]
    sys.modules["masp_bench"] = masp_pkg

    dotenv_mod = types.ModuleType("dotenv")
    dotenv_mod.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = dotenv_mod

    checker_mod = types.ModuleType("masp_bench.checker")
    checker_mod.check_trace = lambda actions, rules, domain="": []
    checker_mod.compute_metrics = lambda *args, **kwargs: SimpleNamespace(
        access_score=1.0,
        op_privacy_score=1.0,
        info_privacy_score=1.0,
        tsr=1.0,
        pqsr=1.0,
        violations_by_code={},
        violations_by_severity={},
    )
    sys.modules["masp_bench.checker"] = checker_mod

    loader_mod = types.ModuleType("masp_bench.loader")
    loader_mod.resolve_task_path = lambda task, project_root, cwd: Path(task)
    loader_mod.load_task_with_tools = lambda task_path: (
        SimpleNamespace(
            task_id="dl-t2",
            domain="daily_life",
            agents=[SimpleNamespace(role="concierge_lead")],
            access_rules=[],
            metadata={},
        ),
        SimpleNamespace(
            tools=[SimpleNamespace(name="mailbox_send", dedup_scope_keys=["to"])]
        ),
    )
    sys.modules["masp_bench.loader"] = loader_mod

    runner_mod = types.ModuleType("masp_bench.runner")

    class BenchmarkRunner:
        def __init__(self, *args, **kwargs) -> None:
            record["runner_init"] = {"args": args, "kwargs": kwargs}

        def run(self, run_id=None):
            record["runner_run_id"] = run_id
            return SimpleNamespace(
                run_id=run_id,
                framework="oai",
                llm="claude-sonnet-4",
                actions=[],
                n_actions=0,
                n_tool_calls=0,
                n_communications=0,
                task_completed=True,
                duration_seconds=0.2,
                error=None,
            )

        def dump_bank_to_file(self, path):
            Path(path).write_text("db", encoding="utf-8")
            return True

        def close_bank(self):
            record["closed_bank"] = True

    runner_mod.BenchmarkRunner = BenchmarkRunner
    runner_mod.framework_run_id_slug = lambda framework: "oa"
    runner_mod.next_run_id = lambda **kwargs: "oa-claude-claude-sonnet-4-test1234"
    sys.modules["masp_bench.runner"] = runner_mod

    trace_mod = types.ModuleType("masp_bench.trace")

    class _Event:
        def __init__(self, **kwargs):
            self.payload = kwargs

    class TraceWriter:
        def __init__(self, path):
            self.path = Path(path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.events = []

        def write_event(self, event):
            self.events.append(event.payload)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event.payload) + "\n")

        def close(self):
            pass

    trace_mod.AccessDecisionEvent = _Event
    trace_mod.TraceEnd = _Event
    trace_mod.TraceWriter = TraceWriter
    sys.modules["masp_bench.trace"] = trace_mod

    workspace_mod = types.ModuleType("masp_bench.workspace")

    class WorkspaceManager:
        def __init__(self, **kwargs):
            record["workspace_init"] = kwargs

    workspace_mod.WorkspaceManager = WorkspaceManager
    sys.modules["masp_bench.workspace"] = workspace_mod

    backbone_mod = types.ModuleType("masp_bench.frameworks.harness_backbone")
    backbone_mod.canonicalize_clawteam_artifacts = lambda **kwargs: (
        kwargs["trace_path"],
        kwargs["run_id"],
        kwargs["current_llm"],
    )
    sys.modules["masp_bench.frameworks.harness_backbone"] = backbone_mod

    spec = importlib.util.spec_from_file_location(
        "masp_bench.cli",
        REPO_ROOT / "masp_bench" / "cli.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_check_trace_signature_drops_unused_tool_scope_keys_argument() -> None:
    checker = _load_checker_module()
    params = inspect.signature(checker.check_trace).parameters

    assert "tool_scope_keys" not in params


def test_cli_run_writes_trace_report_and_bank_artifacts(tmp_path: Path) -> None:
    record: dict[str, object] = {}
    cli = _load_cli_module(record)
    trace_dir = tmp_path / "traces"
    output_dir = tmp_path / "results"
    args = SimpleNamespace(
        task=str(tmp_path / "dl-t2.yaml"),
        repeat=1,
        trace_dir=str(trace_dir),
        output=str(output_dir),
        framework="oai",
        harness="claude",
        model="claude-sonnet-4",
        max_turns=5,
        agent_timeout=30,
    )

    cli.cmd_run(args)

    trace_path = trace_dir / "dl-t2_oa-claude-claude-sonnet-4-test1234.jsonl"
    report_path = output_dir / "dl-t2_oa-claude-claude-sonnet-4-test1234.json"
    db_path = output_dir / "dl-t2_oa-claude-claude-sonnet-4-test1234.sqlite"

    assert trace_path.exists()
    assert report_path.exists()
    assert db_path.exists()

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["run_id"] == "oa-claude-claude-sonnet-4-test1234"
    assert report["trace_file"] == str(trace_path)
