from __future__ import annotations

from masp_bench.frameworks.autogen_adapter import (
    AutoGenAdapter,
    _autogen_task_from_input,
    _message_text,
    _openai_client_kwargs,
)


class _DummyMessage:
    def __init__(self, content):
        self.content = content


def test_autogen_adapter_framework_id() -> None:
    assert AutoGenAdapter.framework_id == "autogen"


def test_autogen_task_mapping_for_multimodal_payload() -> None:
    payload = [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Please review evidence."},
                {"type": "input_image", "image_url": "data:image/png;base64,AAA"},
                {
                    "type": "input_file",
                    "filename": "brief.pdf",
                    "file_data": "data:application/pdf;base64,BBB",
                },
            ],
        }
    ]
    text = _autogen_task_from_input(payload)
    assert "Please review evidence." in text
    assert "[image asset attached]" in text
    assert "[file asset attached: brief.pdf]" in text


def test_message_text_handles_non_string_payload() -> None:
    msg = _DummyMessage({"x": 1, "y": ["a", "b"]})
    text = _message_text(msg)
    assert '"x": 1' in text
    assert '"y"' in text


def test_openai_client_kwargs_adds_model_info_for_gpt5_aliases() -> None:
    kwargs_54 = _openai_client_kwargs("gpt-5.4")
    kwargs_51 = _openai_client_kwargs("gpt-5.1")

    assert kwargs_54["model"] == "gpt-5.4"
    assert kwargs_51["model"] == "gpt-5.1"
    assert "model_info" in kwargs_54
    assert "model_info" in kwargs_51
    assert kwargs_54["model_info"]["family"] == "gpt-5"
    assert kwargs_51["model_info"]["family"] == "gpt-5"


def test_openai_client_kwargs_keeps_standard_model_without_model_info() -> None:
    kwargs = _openai_client_kwargs("gpt-5")
    assert kwargs == {"model": "gpt-5"}
