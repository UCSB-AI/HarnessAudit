#!/usr/bin/env python3
"""MASP_Bench Task Generation Pipeline.

Usage:
    python scripts/generate_tasks.py --domain ecommerce --target-count 20
    python scripts/generate_tasks.py --domain finance --start-stage 1 --dry-run
    python scripts/generate_tasks.py --domain healthcare --batch-id v2
    python scripts/generate_tasks.py --validate-existing --domain finance
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# Auto-load .env file from project root
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from scripts.taskgen.domain_config import DOMAINS
from scripts.taskgen.yaml_io import write_json

log = logging.getLogger("taskgen")


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


def _run_pipeline(args: argparse.Namespace) -> None:
    """Execute the generation pipeline for a single domain."""
    domain = args.domain
    if domain not in DOMAINS:
        log.error("Unknown domain: %s. Available: %s", domain, list(DOMAINS))
        sys.exit(1)

    config = DOMAINS[domain]
    target = args.target_count or config.target_new
    batch_id = args.batch_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    staging_dir = PROJECT_ROOT / "scripts" / "_staging" / domain / batch_id
    staging_dir.mkdir(parents=True, exist_ok=True)
    start_stage = args.start_stage
    dry_run = args.dry_run

    log.info("=" * 60)
    log.info("MASP_Bench Task Generation Pipeline")
    log.info("  Domain:       %s", domain)
    log.info("  Target:       %d new tasks", target)
    log.info("  Batch ID:     %s", batch_id)
    log.info("  Staging:      %s", staging_dir)
    log.info("  Start stage:  %d", start_stage)
    log.info("  Dry run:      %s", dry_run)
    log.info("=" * 60)

    # Stage 0: Inventory
    from scripts.stages.stage_0_inventory import run_stage_0
    inventory = run_stage_0(config, staging_dir)

    # Stage 1: Scenario generation
    if start_stage <= 1:
        from scripts.stages.stage_1_scenario import run_stage_1
        scenarios = run_stage_1(
            inventory, staging_dir,
            target_count=target,
            dry_run=dry_run,
        )
        if dry_run:
            log.info("[dry-run] Pipeline complete. Check staging dir for inventory.")
            return
    else:
        # Load from staging
        scenarios_path = staging_dir / "stage_1_scenarios.json"
        if not scenarios_path.exists():
            log.error("Stage 1 output not found at %s. Run from stage 1 first.", scenarios_path)
            sys.exit(1)
        with open(scenarios_path) as f:
            scenarios = json.load(f)
        log.info("Loaded %d scenarios from staging", len(scenarios))

    if start_stage <= 1 and not dry_run and not args.no_checkpoint:
        log.info("Stage 1 complete: %d scenario candidates generated.", len(scenarios))
        log.info(">>> HUMAN CHECKPOINT: Review scenarios in %s", staging_dir / "stage_1_scenarios.json")
        log.info(">>> Then re-run with --start-stage 2 to continue.")
        return

    # Stage 2: Fixture generation
    if start_stage <= 2:
        from scripts.stages.stage_2_fixtures import run_stage_2
        fixtures = run_stage_2(
            inventory, scenarios, staging_dir, dry_run=dry_run,
        )
        if not dry_run and not args.no_checkpoint:
            log.info("Stage 2 complete: fixtures for %d tasks.", len(fixtures))
            log.info(">>> HUMAN CHECKPOINT: Review fixtures in %s", staging_dir / "stage_2_fixtures.json")
            log.info(">>> Then re-run with --start-stage 3 to continue.")
            return
    else:
        fixtures_path = staging_dir / "stage_2_fixtures.json"
        if not fixtures_path.exists():
            log.error("Stage 2 output not found. Run from stage 2 first.")
            sys.exit(1)
        fixtures = json.load(open(fixtures_path))
        log.info("Loaded fixtures for %d tasks from staging", len(fixtures))

    # Stage 3: Rules + tool necessity + ground truth paths
    if start_stage <= 3:
        from scripts.stages.stage_3_rules import run_stage_3
        rules = run_stage_3(
            inventory, scenarios, fixtures, staging_dir, dry_run=dry_run,
        )
        if not dry_run and not args.no_checkpoint:
            log.info("Stage 3 complete: rules for %d tasks.", len(rules))
            log.info(">>> HUMAN CHECKPOINT: Review rules in %s", staging_dir / "stage_3_rules.json")
            log.info(">>> Then re-run with --start-stage 4 to continue.")
            return
    else:
        rules_path = staging_dir / "stage_3_rules.json"
        if not rules_path.exists():
            log.error("Stage 3 output not found. Run from stage 3 first.")
            sys.exit(1)
        rules = json.load(open(rules_path))
        log.info("Loaded rules for %d tasks from staging", len(rules))

    # Stage 4: Completion checkpoints
    if start_stage <= 4:
        from scripts.stages.stage_4_checkpoints import run_stage_4
        checkpoints = run_stage_4(
            inventory, scenarios, rules, staging_dir, dry_run=dry_run,
        )
    else:
        cp_path = staging_dir / "stage_4_checkpoints.json"
        if not cp_path.exists():
            log.error("Stage 4 output not found. Run from stage 4 first.")
            sys.exit(1)
        checkpoints = json.load(open(cp_path))
        log.info("Loaded checkpoints for %d tasks from staging", len(checkpoints))

    # Stage 5: Multimodal assets
    if start_stage <= 5:
        from scripts.stages.stage_5_multimodal import run_stage_5
        assets = run_stage_5(
            inventory, scenarios, staging_dir, dry_run=dry_run,
        )
    else:
        assets_path = staging_dir / "stage_5_assets.json"
        assets = json.load(open(assets_path)) if assets_path.exists() else {}

    # Stage 6: Recognizer scan (deterministic)
    from scripts.stages.stage_6_recognizer import run_stage_6
    missing_classes = run_stage_6(inventory, rules, staging_dir)
    if missing_classes:
        log.warning(">>> New data_classes need recognizer patterns: %s", missing_classes)
        log.warning(">>> Add patterns to recognizer.py before running validation.")

    # Assembly: combine all stages into final task YAMLs
    from scripts.stages.assemble import assemble_all
    yaml_paths = assemble_all(
        inventory, scenarios, fixtures, rules, checkpoints, assets, staging_dir,
    )

    # Merge stage_2 fixtures into disk fixtures so V5 can find generated IDs
    log.info("Merging stage_2 fixtures into disk fixtures...")
    from scripts.taskgen.yaml_io import merge_seed_json
    from scripts.taskgen.existing_inventory import _extract_ids
    generated_ids: set[str] = set()
    for task_id, task_fixtures in fixtures.items():
        for fname, fdata in task_fixtures.items():
            # Flatten list-format entries (e.g. seed_clients in finance)
            if isinstance(fdata, list):
                flat: dict = {}
                for item in fdata:
                    if isinstance(item, dict):
                        flat.update(item)
                fdata = flat
            if isinstance(fdata, dict) and fdata:
                target = config.fixtures_dir / fname
                merge_seed_json(target, fdata)
        _extract_ids(task_fixtures, generated_ids)

    # Refresh inventory so validators see merged fixtures
    inventory = run_stage_0(config, staging_dir)

    # Validation
    from scripts.validators.run_all import validate_batch
    log.info("Running validation on %d generated tasks...", len(yaml_paths))
    results = validate_batch(
        yaml_paths, inventory,
        skip_load_test=args.skip_load_test,
        extra_ids=generated_ids,
    )
    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    log.info("Validation: %d passed, %d failed out of %d", passed, failed, len(results))

    if failed:
        for r in results:
            if not r.passed:
                log.warning("  FAIL %s:", r.task_id)
                for vname, errs in r.errors.items():
                    for e in errs:
                        log.warning("    [%s] %s", vname, e)

    report = [r.to_dict() for r in results]
    write_json(staging_dir / "validation_report.json", report)
    log.info("Report saved to %s", staging_dir / "validation_report.json")
    log.info("=" * 60)
    log.info("Pipeline complete. %d accepted, %d need repair.", passed, failed)


def _validate_existing(args: argparse.Namespace) -> None:
    """Run validators on all existing tasks for a domain."""
    domain = args.domain
    if domain not in DOMAINS:
        log.error("Unknown domain: %s", domain)
        sys.exit(1)

    config = DOMAINS[domain]

    from scripts.stages.stage_0_inventory import run_stage_0
    staging_dir = PROJECT_ROOT / "scripts" / "_staging" / domain / "validate"
    staging_dir.mkdir(parents=True, exist_ok=True)
    inventory = run_stage_0(config, staging_dir)

    from scripts.validators.run_all import validate_batch

    yaml_paths = sorted(config.tasks_dir.rglob("*.yaml"))
    log.info("Validating %d existing tasks for domain=%s", len(yaml_paths), domain)

    results = validate_batch(yaml_paths, inventory, skip_load_test=args.skip_load_test)

    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    log.info("Results: %d passed, %d failed out of %d", passed, failed, len(results))

    if failed:
        for r in results:
            if not r.passed:
                log.warning("  FAIL %s:", r.task_id)
                for vname, errs in r.errors.items():
                    for e in errs:
                        log.warning("    [%s] %s", vname, e)

    report = [r.to_dict() for r in results]
    write_json(staging_dir / "validation_report.json", report)
    log.info("Report saved to %s", staging_dir / "validation_report.json")


def main() -> None:
    parser = argparse.ArgumentParser(description="MASP_Bench Task Generation Pipeline")
    parser.add_argument("--domain", required=True, help="Target domain")
    parser.add_argument("--target-count", type=int, help="Number of tasks to generate")
    parser.add_argument("--start-stage", type=int, default=0, help="Resume from stage N")
    parser.add_argument("--batch-id", help="Batch identifier (default: timestamp)")
    parser.add_argument("--dry-run", action="store_true", help="Skip LLM calls")
    parser.add_argument("--validate-existing", action="store_true", help="Validate existing tasks only")
    parser.add_argument("--skip-load-test", action="store_true", help="Skip V8 load_test validator")
    parser.add_argument("--no-checkpoint", action="store_true", help="Skip human checkpoints after Stage 2 and 3 (run straight through)")
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()
    _setup_logging(args.verbose)

    if args.validate_existing:
        _validate_existing(args)
    else:
        _run_pipeline(args)


if __name__ == "__main__":
    main()
