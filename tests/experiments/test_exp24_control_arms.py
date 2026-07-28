"""Exp24 control arms: fixed-library and same-budget random.

Gate 2 (per-generation evolution) is decided by Exp24, so its verdict has to be
attributable. Exp22 measured a same-budget random arm at 0.84x the oracle
ceiling, which means "recovery climbs across bins" is also consistent with
"later cases simply got more attempts". These tests lock the properties that
make the controls able to separate those explanations.
"""

from __future__ import annotations

import random
from types import SimpleNamespace

from cmd_audit.counterfactual.actions import PipelineAction
from cmd_audit.counterfactual.operators import OperatorSpec
from experiments import run_experiment_24_operator_trajectory as exp24


def _spec(action: PipelineAction, generation_point: int = 0) -> OperatorSpec:
    return OperatorSpec.single(generation_point, action)


def _entry(action: PipelineAction, case_id: str, net: float = 0.5) -> dict:
    return {
        "fp": "fp-shared",
        "spec": _spec(action),
        "net": net,
        "case_id": case_id,
    }


class SampleRandomShapesTest:
    pass


def test_random_arm_never_draws_the_cases_own_shape() -> None:
    """LOO cleanliness: a control that can replay the case's own accepted shape
    is contaminated, and would flatter the random baseline."""
    library = [
        _entry(PipelineAction.RETRIEVAL_ERROR, "case-self"),
        _entry(PipelineAction.INJECTION_ERROR, "case-other"),
        _entry(PipelineAction.SAFETY_ERROR, "case-other-2"),
    ]

    drawn = exp24._sample_random_shapes(
        library,
        budget=5,
        case_id="case-self",
        rng=random.Random(0),
    )

    formats = {spec.format() for spec in drawn}
    assert _spec(PipelineAction.RETRIEVAL_ERROR).format() not in formats
    assert len(drawn) == 2


def test_random_arm_budget_matches_realized_live_rollouts() -> None:
    """Budget parity must be exact, not nominal: the random arm gets the number
    of executions the live arm actually spent on this case."""
    library = [
        _entry(PipelineAction.RETRIEVAL_ERROR, "c1"),
        _entry(PipelineAction.INJECTION_ERROR, "c2"),
        _entry(PipelineAction.GRANULARITY_ERROR, "c3"),
        _entry(PipelineAction.SAFETY_ERROR, "c4"),
    ]

    for budget in (0, 1, 3):
        drawn = exp24._sample_random_shapes(
            library,
            budget=budget,
            case_id="case-x",
            rng=random.Random(1),
        )
        assert len(drawn) == budget


def test_random_arm_dedupes_identical_shapes() -> None:
    """Several library entries may share a shape; the pool must not let one
    shape occupy the whole budget."""
    library = [
        _entry(PipelineAction.RETRIEVAL_ERROR, "c1"),
        _entry(PipelineAction.RETRIEVAL_ERROR, "c2"),
        _entry(PipelineAction.INJECTION_ERROR, "c3"),
    ]

    drawn = exp24._sample_random_shapes(
        library,
        budget=5,
        case_id="none",
        rng=random.Random(2),
    )

    assert len({spec.format() for spec in drawn}) == len(drawn) == 2


def test_random_arm_is_deterministic_for_a_seed() -> None:
    library = [_entry(a, f"c{i}") for i, a in enumerate(PipelineAction)]

    first = exp24._sample_random_shapes(
        library, budget=4, case_id="x", rng=random.Random(7)
    )
    second = exp24._sample_random_shapes(
        library, budget=4, case_id="x", rng=random.Random(7)
    )

    assert [s.format() for s in first] == [s.format() for s in second]


def test_empty_library_yields_no_random_candidates() -> None:
    assert (
        exp24._sample_random_shapes(
            [], budget=3, case_id="x", rng=random.Random(0)
        )
        == []
    )


# ── shared library stage ────────────────────────────────────────────────


def _recall(n: int = 2) -> tuple:
    return tuple(
        SimpleNamespace(
            memory_id=f"m{i}",
            text=f"memory {i}",
            store="episodic",
            passed_safety_filter=True,
            is_graph_expanded=False,
            source_event_ids=(f"e{i}",),
        )
        for i in range(n)
    )


def test_library_stage_stops_at_first_recovering_shape() -> None:
    """accept-if-improves: execution stops as soon as a shape clears the
    threshold, so cost is the rank at which recovery happened."""
    calls: list[str] = []

    def execute(spec: OperatorSpec) -> float:
        calls.append(spec.format())
        return 0.9

    net, spec, rollouts, rank = exp24._run_library_stage(
        [
            _spec(PipelineAction.RETRIEVAL_ERROR),
            _spec(PipelineAction.INJECTION_ERROR),
        ],
        execute=execute,
        net_gain=lambda score: score,
        recall=_recall(),
        max_depth=2,
        item_pool=("m0", "m1"),
        threshold=0.1,
    )

    assert rollouts == 1
    assert rank == 1
    assert spec is not None
    assert net == 0.9
    assert len(calls) == 1


def test_library_stage_timeout_never_wins_and_is_skipped() -> None:
    """A NaN score must not become the best operator regardless of position."""
    scores = [float("nan"), 0.4]

    def execute(spec: OperatorSpec) -> float:
        return scores.pop(0)

    net, spec, rollouts, rank = exp24._run_library_stage(
        [
            _spec(PipelineAction.RETRIEVAL_ERROR),
            _spec(PipelineAction.INJECTION_ERROR),
        ],
        execute=execute,
        net_gain=lambda score: score,
        recall=_recall(),
        max_depth=2,
        item_pool=("m0", "m1"),
        threshold=0.1,
    )

    assert rollouts == 2
    assert rank == 2
    assert net == 0.4
    assert spec is not None


def test_score_candidates_executes_every_legal_candidate_once() -> None:
    """The whole retrieved set is scored so the random-ORDER control costs no
    extra LLM calls."""
    calls: list[str] = []

    def execute(spec: OperatorSpec) -> float:
        calls.append(spec.format())
        return 0.9

    scored = exp24._score_candidates(
        [
            _spec(PipelineAction.RETRIEVAL_ERROR),
            _spec(PipelineAction.INJECTION_ERROR),
            _spec(PipelineAction.SAFETY_ERROR),
        ],
        execute=execute,
        net_gain=lambda score: score,
        recall=_recall(),
        max_depth=2,
        item_pool=("m0", "m1"),
    )

    assert len(calls) == 3
    assert len(scored) == 3


def test_replay_early_stop_matches_run_library_stage_semantics() -> None:
    """The cached replay must reproduce accept-if-improves exactly, otherwise
    the live arm's reported cost would drift from deployment behaviour."""
    specs = [
        _spec(PipelineAction.RETRIEVAL_ERROR),
        _spec(PipelineAction.INJECTION_ERROR),
        _spec(PipelineAction.SAFETY_ERROR),
    ]
    scores = {0: 0.0, 1: 0.6, 2: 0.9}
    calls = {"n": 0}

    def execute(spec: OperatorSpec) -> float:
        value = scores[calls["n"]]
        calls["n"] += 1
        return value

    direct = exp24._run_library_stage(
        specs,
        execute=execute,
        net_gain=lambda s: s,
        recall=_recall(),
        max_depth=2,
        item_pool=("m0", "m1"),
        threshold=0.1,
    )

    replayed = exp24._replay_early_stop(
        [(specs[i], scores[i]) for i in range(3)], threshold=0.1
    )

    # best_net, rollouts, rank must agree; spec identity compared by format.
    assert replayed[0] == direct[0]
    assert replayed[2] == direct[2]
    assert replayed[3] == direct[3]
    assert replayed[1] is not None and direct[1] is not None
    assert replayed[1].format() == direct[1].format()


def test_random_order_ties_on_recovery_but_can_differ_on_cost() -> None:
    """Both arms walk the SAME candidate set and stop at the first hit, so
    whether the case recovers is identical by construction. Only the rank
    differs -- which is why this arm is a cost comparison, not a rate one."""
    specs = [_spec(a) for a in (
        PipelineAction.RETRIEVAL_ERROR,
        PipelineAction.INJECTION_ERROR,
        PipelineAction.SAFETY_ERROR,
        PipelineAction.GRANULARITY_ERROR,
    )]
    scored = [
        (specs[0], 0.0),
        (specs[1], 0.0),
        (specs[2], 0.7),
        (specs[3], 0.0),
    ]

    live = exp24._replay_early_stop(scored, threshold=0.1)
    reordered = exp24._replay_early_stop(
        [scored[2], scored[0], scored[1], scored[3]], threshold=0.1
    )

    # Same recovery outcome...
    assert (live[1] is not None) == (reordered[1] is not None)
    # ...different cost.
    assert live[3] == 3
    assert reordered[3] == 1


def test_random_order_finds_nothing_when_the_set_has_nothing() -> None:
    scored = [(_spec(PipelineAction.RETRIEVAL_ERROR), 0.0)]

    for order in (scored, list(reversed(scored))):
        best, spec, rollouts, rank = exp24._replay_early_stop(
            order, threshold=0.1
        )
        assert spec is None
        assert rank == 0
        assert rollouts == 1


def test_library_stage_returns_zero_when_nothing_recovers() -> None:
    net, spec, rollouts, rank = exp24._run_library_stage(
        [_spec(PipelineAction.RETRIEVAL_ERROR)],
        execute=lambda spec: 0.0,
        net_gain=lambda score: score,
        recall=_recall(),
        max_depth=2,
        item_pool=("m0", "m1"),
        threshold=0.1,
    )

    assert (net, spec, rank) == (0.0, None, 0)
    assert rollouts == 1


# ── summary aggregation ─────────────────────────────────────────────────


def _row(**overrides) -> dict[str, str]:
    row = {
        "case_index": "1",
        "generation_bin": "1",
        "excluded": "false",
        "timeout_count": "0",
        "recovered": "true",
        "recovery_source": "library",
        "library_size_before": "3",
        "total_rollouts": "2",
        "fixed_recovered": "true",
        "random_recovered": "false",
    }
    row.update(overrides)
    return row


def test_summary_reports_all_three_arm_rates() -> None:
    rows = [
        _row(case_index="1"),
        _row(
            case_index="2",
            recovered="false",
            recovery_source="unrecovered",
            fixed_recovered="false",
            random_recovered="false",
        ),
    ]

    summary = exp24._summary_rows(rows, bin_size=2)[0]

    assert summary["recovery_rate"] == "0.5000"
    assert summary["fixed_recovery_rate"] == "0.5000"
    assert summary["random_recovery_rate"] == "0.0000"


def test_excluded_case_leaves_every_arm_denominator() -> None:
    """A NaN identity baseline makes every arm's net NaN. The case must be
    dropped from the live arm AND both controls, never counted as a failure --
    that is the downward bias the NaN sentinel exists to remove."""
    # The excluded row carries NON-blank control values on purpose. A blank
    # would pass even if the aggregator filtered the wrong collection, so this
    # fixture is what gives the test its discriminating power: only filtering
    # on `included` keeps the excluded row out of the control denominators.
    rows = [
        _row(case_index="1"),
        _row(
            case_index="2",
            excluded="true",
            timeout_count="1",
            recovered="false",
            recovery_source="excluded",
            fixed_recovered="false",
            random_recovered="false",
        ),
    ]

    summary = exp24._summary_rows(rows, bin_size=2)[0]

    assert summary["cases"] == "1"
    assert summary["excluded_cases"] == "1"
    assert summary["recovery_rate"] == "1.0000"
    # 1/1, not 1/2: the excluded case must not dilute either control.
    assert summary["fixed_recovery_rate"] == "1.0000"
    assert summary["random_recovery_rate"] == "0.0000"
    assert summary["fixed_recovered"] == "1"
    assert summary["random_recovered"] == "0"


def test_controls_off_leaves_rates_blank_not_zero() -> None:
    """With --controls off the arms did not run. Blank must never be read as
    "the control scored zero", which would fabricate a beaten baseline."""
    rows = [_row(fixed_recovered="", random_recovered="")]

    summary = exp24._summary_rows(rows, bin_size=1)[0]

    assert summary["fixed_recovery_rate"] == ""
    assert summary["random_recovery_rate"] == ""
    assert summary["recovery_rate"] == "1.0000"


def test_excluded_detail_row_blanks_control_columns() -> None:
    row = exp24._excluded_detail_row(
        case_index=2,
        generation_bin=1,
        case=SimpleNamespace(
            case_id="c-timeout", perturbation_label="safety_error"
        ),
        library_size_before=5,
    )

    assert row["fixed_recovered"] == ""
    assert row["random_recovered"] == ""
    assert set(row) <= set(exp24._detail_fieldnames())


def test_detail_and_summary_fieldnames_cover_control_columns() -> None:
    """csv.DictWriter raises on stray keys, so row dicts and fieldnames must
    stay in sync."""
    detail = exp24._detail_fieldnames()
    for column in (
        "fixed_recovered",
        "fixed_net",
        "fixed_rollouts",
        "fixed_library_size_before",
        "random_recovered",
        "random_net",
        "random_rollouts",
    ):
        assert column in detail

    summary_row = exp24._summary_rows([_row()], bin_size=1)[0]
    assert set(summary_row) == set(exp24._summary_fieldnames())
