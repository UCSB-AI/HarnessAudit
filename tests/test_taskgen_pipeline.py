from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_inventory_collects_current_non_sde_distribution() -> None:
    from scripts.taskgen.existing_inventory import collect_inventory

    inventory = collect_inventory(Path.cwd())

    assert inventory.model_name == "gpt-5.4-xhigh"
    assert inventory.total_non_sde_tasks == 48
    assert inventory.domain_task_counts == {
        "daily_life": 4,
        "ecommerce": 5,
        "finance": 10,
        "healthcare": 6,
        "legal_compliance": 7,
        "office": 9,
        "social_interaction": 7,
    }
    assert inventory.tool_count > 80
    assert "ecommerce" in inventory.tool_names_by_domain
    assert "ec-t1" in inventory.task_ids


def test_tool_policy_validator_enforces_required_union_and_partition() -> None:
    from scripts.taskgen.validators.tool_policy import validate_tool_policy

    report = validate_tool_policy(
        task_id="demo-t1",
        catalog_tools=["a", "b", "c", "d"],
        agents=[
            {
                "role": "worker",
                "tool_necessity": {
                    "required": ["a", "b", "c"],
                    "forbidden": ["d"],
                    "unnecessary": [],
                },
            }
        ],
        ground_truth_tool_paths={"worker": [["a", "b"], ["c", "b"]]},
    )

    assert report.ok
    assert report.issues == []


def test_tool_policy_validator_rejects_missing_required_path_tool() -> None:
    from scripts.taskgen.validators.tool_policy import validate_tool_policy

    report = validate_tool_policy(
        task_id="demo-t1",
        catalog_tools=["a", "b", "c"],
        agents=[
            {
                "role": "worker",
                "tool_necessity": {
                    "required": ["a"],
                    "forbidden": [],
                    "unnecessary": ["b", "c"],
                },
            }
        ],
        ground_truth_tool_paths={"worker": [["a", "b"]]},
    )

    assert not report.ok
    assert any("required must equal union" in issue.message for issue in report.issues)


def test_cli_dry_run_writes_staging_files(tmp_path: Path) -> None:
    from scripts.generate_tasks import main

    exit_code = main(
        [
            "--domain",
            "ecommerce",
            "--target-count",
            "1",
            "--dry-run",
            "--batch-id",
            "unit",
            "--staging-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    batch_dir = tmp_path / "ecommerce" / "unit"
    inventory_path = batch_dir / "stage_0_inventory.json"
    report_path = batch_dir / "validation_report.json"
    scenarios_path = batch_dir / "stage_1_scenarios.json"

    assert inventory_path.is_file()
    assert report_path.is_file()
    assert scenarios_path.is_file()

    report = json.loads(report_path.read_text())
    assert report["domain"] == "ecommerce"
    assert report["dry_run"] is True
    assert report["model_name"] == "gpt-5.4-xhigh"
    assert report["target_count"] == 1
    assert report["stages_completed"] == ["inventory", "scenario_dry_run"]
