from __future__ import annotations

from pathlib import Path

from masp_bench.loader import load_task_with_tools, resolve_task_path
from masp_bench.recognizer import contains
from masp_bench.schemas.access_rules import AccessDecision, AccessMatch, AccessRule, ViolationCode
from masp_bench.schemas.actions import ActionSurface, ObservableAction
from masp_bench.schemas.trace import RunMetrics


def test_resolve_task_path_supports_ecommerce_and_office_dirs(tmp_path) -> None:
    tasks_dir = tmp_path / "tasks"
    ecommerce_dir = tasks_dir / "ecommerce"
    office_dir = tasks_dir / "office"
    ecommerce_dir.mkdir(parents=True)
    office_dir.mkdir(parents=True)

    ecommerce_file = ecommerce_dir / "ec-t1.yaml"
    office_file = office_dir / "off-t1.yaml"
    ecommerce_file.write_text("task_id: ec-t1\n")
    office_file.write_text("task_id: off-t1\n")

    assert resolve_task_path("ec-t1.yaml", project_root=tmp_path, cwd=tmp_path) == ecommerce_file.resolve()
    assert resolve_task_path("off-t1.yaml", project_root=tmp_path, cwd=tmp_path) == office_file.resolve()


def test_run_metrics_dedup_key_uses_data_class_for_v_id() -> None:
    action = ObservableAction(
        sequence_num=1,
        run_id="run-1",
        surface=ActionSurface.COMMUNICATION,
        agent_id="commerce_ops_manager",
        agent_role="commerce_ops_manager",
        target_agent="identity_verifier",
        target_role="identity_verifier",
        content="Please use nina.patel@retailco.example when checking the account.",
    )
    rule = AccessRule(
        role="commerce_ops_manager",
        access_type="information.data_leak",
        match=AccessMatch(data_class="customer_email", forbidden_to=["identity_verifier"]),
        effect="redact",
        severity="high",
    )
    decision = AccessDecision(
        violated=True,
        rule=rule,
        violation_code=ViolationCode.V_ID,
        effect="redact",
        severity="high",
        action_sequence_num=1,
    )

    score = RunMetrics.compute_access_score([action], [decision])

    assert score == 0.7


def test_recognizer_supports_new_domains() -> None:
    assert contains("nina.patel@retailco.example", "ecommerce", "customer_email")
    assert contains("+1-555-101-2020", "office_enterprise_management", "executive_contact")


def test_new_domain_tasks_load_from_repo() -> None:
    project_root = Path(__file__).resolve().parents[1]
    for task_name in (
        "ec-t1.yaml",
        "ec-t2.yaml",
        "ec-t3.yaml",
        "ec-t4.yaml",
        "ec-t5.yaml",
        "off-t1.yaml",
        "off-t2.yaml",
        "off-t3.yaml",
        "off-t4.yaml",
        "off-t5.yaml",
        "off-t6.yaml",
    ):
        task, catalog = load_task_with_tools(task_name, project_root=project_root, cwd=project_root)
        assert len(task.agents) >= 3
        assert len(catalog.tools) >= 1
