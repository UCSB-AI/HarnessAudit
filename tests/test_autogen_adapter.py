from masp_bench.frameworks.autogen_adapter import _openai_client_kwargs


def test_openai_client_kwargs_normalizes_gpt_5_4_alias_and_keeps_model_info() -> None:
    kwargs = _openai_client_kwargs("gpt-5.4")

    assert kwargs["model"] == "gpt-5.4-2026-03-05"
    assert kwargs["model_info"] == {
        "vision": True,
        "function_calling": True,
        "json_output": True,
        "family": "gpt-5",
        "structured_output": True,
        "multiple_system_messages": True,
    }


def test_openai_client_kwargs_normalizes_gpt_5_1_alias_and_keeps_model_info() -> None:
    kwargs = _openai_client_kwargs("gpt-5.1")

    assert kwargs["model"] == "gpt-5.1-2025-11-13"
    assert kwargs["model_info"] == {
        "vision": True,
        "function_calling": True,
        "json_output": True,
        "family": "gpt-5",
        "structured_output": True,
        "multiple_system_messages": True,
    }


def test_openai_client_kwargs_leaves_other_models_unchanged() -> None:
    kwargs = _openai_client_kwargs("gpt-4.1")

    assert kwargs == {"model": "gpt-4.1"}
