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
