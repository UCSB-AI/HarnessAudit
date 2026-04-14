from __future__ import annotations

from pathlib import Path

from masp_bench.banks import get_bank
from masp_bench.loader import load_task_with_tools


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_new_domain_banks_seed_counts() -> None:
    checks = [
        ("daily_life", "SELECT COUNT(*) AS n FROM requests", 4),
        ("social_interaction", "SELECT COUNT(*) AS n FROM cases", 4),
        ("legal_compliance", "SELECT COUNT(*) AS n FROM matters", 4),
        ("real_estate", "SELECT COUNT(*) AS n FROM cases", 4),
        ("journalism_media", "SELECT COUNT(*) AS n FROM stories", 4),
    ]

    for domain, query, expected in checks:
        bank = get_bank(domain)
        try:
            rows = bank.query_sql(query)
            assert rows == [{"n": expected}]
        finally:
            bank.close()


def test_all_five_domain_tasks_load_with_tools() -> None:
    task_paths = sorted((PROJECT_ROOT / "tasks" / "daily_life").glob("*.yaml"))
    task_paths += sorted((PROJECT_ROOT / "tasks" / "social_interaction").glob("*.yaml"))
    task_paths += sorted((PROJECT_ROOT / "tasks" / "legal_compliance").glob("*.yaml"))
    task_paths += sorted((PROJECT_ROOT / "tasks" / "real_estate").glob("*.yaml"))
    task_paths += sorted((PROJECT_ROOT / "tasks" / "journalism_media").glob("*.yaml"))

    assert len(task_paths) == 20

    for task_path in task_paths:
        task, catalog = load_task_with_tools(task_path)
        assert task.task_id
        assert task.domain
        assert catalog.domain == task.domain
        assert catalog.tools
        assert task.access_rules


def test_new_multimodal_tasks_resolve_pdf_assets() -> None:
    for rel_path, expected_assets in [
        ("tasks/real_estate/re-t4.yaml", 4),
        ("tasks/journalism_media/jm-t4.yaml", 4),
    ]:
        task, _catalog = load_task_with_tools(PROJECT_ROOT / rel_path)
        assert task.modality == "multimodal"
        assert len(task.input_assets) == expected_assets
        assert all(Path(asset.resolved_path or "").is_file() for asset in task.input_assets)


def test_multimodal_backfill_tasks_use_local_task_assets() -> None:
    expected_multimodal = [
        "tasks/social_interaction/si-t1.yaml",
        "tasks/social_interaction/si-t2.yaml",
        "tasks/legal_compliance/lc-t1.yaml",
        "tasks/legal_compliance/lc-t2.yaml",
        "tasks/legal_compliance/lc-t4.yaml",
        "tasks/real_estate/re-t2.yaml",
        "tasks/real_estate/re-t4.yaml",
        "tasks/journalism_media/jm-t2.yaml",
        "tasks/journalism_media/jm-t3.yaml",
        "tasks/journalism_media/jm-t4.yaml",
    ]

    for rel_path in expected_multimodal:
        task_path = PROJECT_ROOT / rel_path
        task, _catalog = load_task_with_tools(task_path)
        assert task.modality == "multimodal"
        assert task.input_assets
        expected_asset_root = task_path.parent / "assets" / task.task_id
        for asset in task.input_assets:
            resolved = Path(asset.resolved_path or "")
            assert resolved.is_file()
            assert expected_asset_root in resolved.parents
