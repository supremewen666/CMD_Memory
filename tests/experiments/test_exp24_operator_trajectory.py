from __future__ import annotations

from types import SimpleNamespace

from cmd_audit.counterfactual.actions import PipelineAction
from cmd_audit.counterfactual.operators import OperatorSpec
from experiments import run_experiment_24_operator_trajectory as exp24


def test_retrieve_library_retains_multiple_shapes_and_uses_recovery_tiebreak(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        exp24,
        "_query_signature_similarity",
        lambda left, right: 1.0 if left == right else 0.0,
    )
    weaker = OperatorSpec.single(0, PipelineAction.RETRIEVAL_ERROR)
    stronger = OperatorSpec.single(1, PipelineAction.INJECTION_ERROR)
    duplicate_stronger = OperatorSpec.single(
        1,
        PipelineAction.INJECTION_ERROR,
    )
    library = [
        {"fp": "cluster", "spec": weaker, "net": 0.2},
        {"fp": "cluster", "spec": stronger, "net": 0.8},
        {"fp": "cluster", "spec": duplicate_stronger, "net": 0.4},
    ]

    retrieved = exp24._retrieve_library(
        library,
        "cluster",
        topn=5,
    )

    assert [spec.format() for spec in retrieved] == [
        stronger.format(),
        weaker.format(),
    ]


def test_legalize_drops_illegal_steps_and_unknown_item_hints(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        exp24,
        "get_legal_actions",
        lambda _recall, generation_point: (
            [PipelineAction.IDENTITY, PipelineAction.RETRIEVAL_ERROR]
            if generation_point == 0
            else [PipelineAction.IDENTITY]
        ),
    )
    spec = OperatorSpec.from_actions(
        (
            (0, PipelineAction.RETRIEVAL_ERROR),
            (1, PipelineAction.INJECTION_ERROR),
        ),
        item_signal_hints={"known": 1.0, "missing": -1.0},
    )

    grounded = exp24._legalize(
        spec,
        (),
        max_depth=2,
        item_pool={"known"},
    )

    assert grounded is not None
    assert grounded.format() == "gp0:retrieval_error+hints[known=1]"


def test_summary_excludes_identity_timeout_from_recovery_denominator() -> None:
    rows = [
        {
            "case_index": "1",
            "generation_bin": "1",
            "excluded": "false",
            "timeout_count": "0",
            "recovered": "true",
            "recovery_source": "library",
            "library_size_before": "2",
            "total_rollouts": "1",
        },
        {
            "case_index": "2",
            "generation_bin": "1",
            "excluded": "true",
            "timeout_count": "1",
            "recovered": "false",
            "recovery_source": "excluded",
            "library_size_before": "2",
            "total_rollouts": "0",
        },
    ]

    summary = exp24._summary_rows(rows, bin_size=2)

    assert summary == [
        {
            "generation_bin": "1",
            "bin_start": "1",
            "bin_end": "2",
            "cases": "1",
            "excluded_cases": "1",
            "timeout_count": "1",
            "recovered": "1",
            "recovery_rate": "1.0000",
            "library_recovered": "1",
            "library_recovery_rate": "1.0000",
            # Control arms absent from these legacy rows: the arms did not run,
            # so their counts are 0 and their rates blank rather than 0.0000.
            # A blank rate must never be read as "the control scored zero".
                "fixed_recovered": "0",
                "fixed_recovery_rate": "",
                "fixed_library_recovery_rate": "",
                "random_recovered": "0",
                "random_recovery_rate": "",
                "random_library_recovery_rate": "",
            "avg_library_size": "2.0000",
            "avg_total_rollouts": "1.0000",
            "avg_rollouts_recovered_cases": "1.0000",
        }
    ]


def test_excluded_row_records_a1_timeout_contract() -> None:
    row = exp24._excluded_detail_row(
        case_index=3,
        generation_bin=1,
        case=SimpleNamespace(
            case_id="case-timeout",
            perturbation_label="retrieval_error",
        ),
        library_size_before=4,
    )

    assert row["status"] == "base_gain_timeout"
    assert row["excluded"] == "true"
    assert row["timeout_count"] == "1"
    assert row["best_net_gain"] == ""
    assert row["library_written"] == "false"
