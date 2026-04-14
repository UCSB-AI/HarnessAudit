from __future__ import annotations

import base64
from pathlib import Path

import yaml

from masp_bench.loader import load_task, resolve_task_path
from masp_bench.runner import build_task_input
from masp_bench.schemas.task import TaskConfig


def _minimal_task_dict() -> dict:
    return {
        "task_id": "sample-t1",
        "domain": "finance",
        "goal": "Summarize the attached evidence and complete the task.",
        "agents": [
            {
                "role": "hub_role",
                "description": "Hub agent",
            },
        ],
        "access_rules": [],
        "success_checks": [],
    }


def test_load_task_resolves_multimodal_asset_paths(tmp_path: Path) -> None:
    image_path = tmp_path / "evidence.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake-image")
    pdf_path = tmp_path / "brief.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nfake-pdf")

    task_path = tmp_path / "task.yaml"
    payload = _minimal_task_dict()
    payload["modality"] = "multimodal"
    payload["input_assets"] = [
        {
            "asset_type": "image",
            "path": "evidence.png",
            "detail": "high",
            "description": "Primary incident screenshot",
        },
        {
            "asset_type": "pdf",
            "path": "brief.pdf",
            "description": "Supporting incident report",
        },
    ]
    task_path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    task = load_task(task_path)

    assert task.modality == "multimodal"
    assert len(task.input_assets) == 2
    assert task.input_assets[0].asset_type == "image"
    assert Path(task.input_assets[0].resolved_path or "").resolve() == image_path.resolve()
    assert task.input_assets[1].asset_type == "pdf"
    assert Path(task.input_assets[1].resolved_path or "").resolve() == pdf_path.resolve()


def test_resolve_task_path_supports_new_domain_directories(tmp_path: Path) -> None:
    task_path = tmp_path / "tasks" / "social_interaction" / "si-t1.yaml"
    task_path.parent.mkdir(parents=True)
    task_path.write_text("task_id: si-t1\n", encoding="utf-8")

    resolved = resolve_task_path("si-t1.yaml", project_root=tmp_path, cwd=tmp_path)

    assert resolved == task_path.resolve()


def test_build_task_input_returns_goal_for_text_only_task() -> None:
    task = TaskConfig(**_minimal_task_dict())

    assert build_task_input(task) == task.goal


def test_build_task_input_creates_multimodal_user_message(tmp_path: Path) -> None:
    image_bytes = b"\x89PNG\r\n\x1a\nmultimodal-image"
    pdf_bytes = b"%PDF-1.4\nmultimodal-pdf"
    image_path = tmp_path / "incident.png"
    pdf_path = tmp_path / "incident.pdf"
    image_path.write_bytes(image_bytes)
    pdf_path.write_bytes(pdf_bytes)

    payload = _minimal_task_dict()
    payload["modality"] = "multimodal"
    payload["input_assets"] = [
        {
            "asset_type": "image",
            "path": str(image_path),
            "resolved_path": str(image_path),
            "detail": "high",
            "description": "Annotated screenshot",
        },
        {
            "asset_type": "pdf",
            "path": str(pdf_path),
            "resolved_path": str(pdf_path),
            "description": "Dispute packet",
        },
    ]
    task = TaskConfig(**payload)

    result = build_task_input(task)

    assert isinstance(result, list)
    assert len(result) == 1
    message = result[0]
    assert message["role"] == "user"
    content = message["content"]
    assert content[0] == {
        "type": "input_text",
        "text": task.goal,
    }
    assert content[1]["type"] == "input_text"
    assert "Annotated screenshot" in content[1]["text"]
    assert content[2]["type"] == "input_image"
    assert content[2]["detail"] == "high"
    assert content[2]["image_url"] == (
        "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii")
    )
    assert content[3]["type"] == "input_text"
    assert "Dispute packet" in content[3]["text"]
    assert content[4] == {
        "type": "input_file",
        "file_data": (
            "data:application/pdf;base64,"
            + base64.b64encode(pdf_bytes).decode("ascii")
        ),
        "filename": "incident.pdf",
    }
