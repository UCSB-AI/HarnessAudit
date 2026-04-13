from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "summarize_sde_scores.py"


def _write_result(
    results_dir: Path,
    stem: str,
    *,
    llm: str,
    privacy: float,
    access: float,
    operation: float,
    information: float,
) -> None:
    (results_dir / f"{stem}.json").write_text(
        json.dumps(
            {
                "llm": llm,
                "privacy_score": privacy,
                "access_score": access,
                "operation_score": operation,
                "information_score": information,
            }
        )
    )


def test_summary_reports_all_five_domains_and_uses_latest_run(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"
    results_dir.mkdir()

    _write_result(
        results_dir,
        "fin-t1_oai-gpt-5.4-1",
        llm="old-fin",
        privacy=0.1,
        access=0.1,
        operation=0.1,
        information=0.1,
    )
    _write_result(
        results_dir,
        "fin-t1_oai-gpt-5.4-2",
        llm="new-fin",
        privacy=0.9,
        access=0.8,
        operation=0.7,
        information=0.6,
    )
    _write_result(
        results_dir,
        "ec-t1_oai-gpt-5.4-1",
        llm="ec-model",
        privacy=0.5,
        access=0.5,
        operation=0.5,
        information=0.5,
    )
    _write_result(
        results_dir,
        "off-t1_oai-gpt-5.1-1",
        llm="off-model",
        privacy=0.4,
        access=0.3,
        operation=0.2,
        information=0.1,
    )
    _write_result(
        results_dir,
        "sde1-t1_oai-gpt-5.4-1",
        llm="sde-model",
        privacy=0.7,
        access=0.6,
        operation=0.5,
        information=0.4,
    )
    _write_result(
        results_dir,
        "healthcare-t1_oai-gpt-5.4-1",
        llm="health-model",
        privacy=0.8,
        access=0.7,
        operation=0.6,
        information=0.5,
    )

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(results_dir)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr
    assert "## fin" in proc.stdout
    assert "## ec" in proc.stdout
    assert "## healthcare" in proc.stdout
    assert "## off" in proc.stdout
    assert "## sde" in proc.stdout
    assert "fin-t1\tgpt-5.4\tnew-fin\t0.9000\t0.8000\t0.7000\t0.6000" in proc.stdout
    assert "old-fin" not in proc.stdout


def test_summary_keeps_empty_domains_visible(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"
    results_dir.mkdir()

    _write_result(
        results_dir,
        "fin-t1_oai-gpt-5.4-1",
        llm="fin-model",
        privacy=0.9,
        access=0.8,
        operation=0.7,
        information=0.6,
    )
    _write_result(
        results_dir,
        "sde1-t1_oai-gpt-5.1-1",
        llm="sde-model",
        privacy=0.5,
        access=0.4,
        operation=0.3,
        information=0.2,
    )

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(results_dir)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr
    assert "## healthcare" in proc.stdout
    assert "## ec" in proc.stdout
    assert "## off" in proc.stdout
    assert "(无数据)" in proc.stdout
