from __future__ import annotations

import hashlib
import importlib.util
import sys
import types
from pathlib import Path


def _load_harness_profiles_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "masp_bench" / "frameworks" / "harness_profiles.py"

    masp_pkg = types.ModuleType("masp_bench")
    masp_pkg.__path__ = [str(repo_root / "masp_bench")]
    sys.modules.setdefault("masp_bench", masp_pkg)

    frameworks_pkg = types.ModuleType("masp_bench.frameworks")
    frameworks_pkg.__path__ = [str(repo_root / "masp_bench" / "frameworks")]
    sys.modules.setdefault("masp_bench.frameworks", frameworks_pkg)

    spec = importlib.util.spec_from_file_location(
        "masp_bench.frameworks.harness_profiles",
        module_path,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


harness_profiles = _load_harness_profiles_module()
session_name = harness_profiles.session_name


def test_openclaw_session_name_is_shortened_stably_when_too_long() -> None:
    run_id = "ct-openclaw-claude-sonnet-4-197ae28f"
    agent_name = "payments_and_coverage_specialist"
    candidate = f"{run_id}-{agent_name}"
    digest = hashlib.sha1(candidate.encode("utf-8")).hexdigest()[:8]
    expected = f"{candidate[:55]}-{digest}"

    actual = session_name(run_id, agent_name, "openclaw")

    assert actual == expected
    assert len(actual) == 64
    assert actual == session_name(run_id, agent_name, "openclaw")


def test_non_openclaw_session_names_keep_full_readable_identifier() -> None:
    actual = session_name(
        "ct-codex-claude-sonnet-4-197ae28f",
        "payments_and_coverage_specialist",
        "codex",
    )

    assert actual == (
        "ct-codex-claude-sonnet-4-197ae28f-payments_and_coverage_specialist"
    )
