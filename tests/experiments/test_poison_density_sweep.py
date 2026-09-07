"""task.md 3.5 — the poisoning-density boundary must be visible, not assumed."""

from __future__ import annotations

import pytest

from experiments.poison_density_sweep import (
    DETECTORS,
    MINORITY_ASSUMPTION_LIMIT,
    PoisonSweepError,
    build_sweep_case,
    format_grid,
    run_sweep,
    score_detector,
)


def _cell(report, detector: str, density: float):
    return next(
        row
        for row in report["grid"]
        if row["detector"] == detector and row["density"] == pytest.approx(density)
    )


def test_every_detector_is_clean_on_an_unpoisoned_store() -> None:
    cases = (build_sweep_case(case_id="c0", recall_size=10, poisoned_count=0),)

    for detector in DETECTORS:
        score = score_detector(detector, cases, threshold=0.6)
        # No poison exists, so any flag is a false alarm on healthy memory.
        assert score.false_positive == 0
        assert score.true_positive == 0
        assert not score.inverted


def test_all_detectors_agree_while_the_poison_stays_a_minority() -> None:
    report = run_sweep(recall_size=10, max_density=0.4)

    for detector in DETECTORS:
        for density in (0.1, 0.2, 0.3, 0.4):
            assert _cell(report, detector, density)["f1"] == 1.0
    # Below the limit the sweep cannot separate the rules — which is exactly why
    # the default sweep runs past it.
    assert report["swept_past_minority_limit"] is False


def test_endogenous_reference_rules_invert_past_the_minority_limit() -> None:
    report = run_sweep(recall_size=10, max_density=0.9)

    for detector in ("minority_vote", "loo_reconstruction"):
        summary = report["summary"][detector]
        # Both fail, and they fail in the dangerous direction: they flag the
        # clean items once the poison becomes the majority.
        assert summary["first_inverted_density"] == pytest.approx(0.6)
        assert summary["min_f1_over_poisoned_cells"] == 0.0
        assert _cell(report, detector, 0.9)["inverted"] is True

    anchored = report["summary"]["anchored_contrast"]
    # The exogenous reference cannot be poisoned by the store, so density does
    # not move it.
    assert anchored["first_inverted_density"] is None
    assert anchored["min_f1_over_poisoned_cells"] == 1.0
    assert report["swept_past_minority_limit"] is True


def test_loo_degrades_before_it_inverts_at_the_exact_tie() -> None:
    report = run_sweep(recall_size=10, max_density=0.5)

    # At a 5/5 split the minority rule has no smaller cluster and abstains,
    # while LOO still gets partial credit — the two failures are not the same
    # failure, so the sweep must not report them as one.
    assert _cell(report, "minority_vote", 0.5)["f1"] == 0.0
    assert 0.0 < _cell(report, "loo_reconstruction", 0.5)["f1"] < 1.0
    assert _cell(report, "anchored_contrast", 0.5)["f1"] == 1.0
    assert MINORITY_ASSUMPTION_LIMIT == 0.5


def test_no_detector_reads_the_ground_truth_poison_flag() -> None:
    from experiments import poison_density_sweep as module

    case = build_sweep_case(case_id="c0", recall_size=6, poisoned_count=2)
    # Flip the construction record without touching any text. A detector that
    # reads `poisoned` would change its answer; one that reads only text cannot.
    lying = type(case)(
        case_id=case.case_id,
        query=case.query,
        items=tuple(
            type(row)(memory_id=row.memory_id, text=row.text, poisoned=not row.poisoned)
            for row in case.items
        ),
        anchor_text=case.anchor_text,
    )
    for detector in DETECTORS:
        assert module._detect(detector, case, threshold=0.6) == module._detect(
            detector, lying, threshold=0.6
        )


def test_report_is_zero_call_and_declares_its_oracle() -> None:
    report = run_sweep(recall_size=6, max_density=0.5, cases_per_cell=2)

    assert report["model_calls"] == 0
    # The scope caveat has to travel with the numbers: this is a decision-rule
    # sweep with judge noise held at zero, not an end-to-end detector result.
    assert "not_geval_judge" in report["divergence_oracle"]
    assert report["poisoning_regime"] == "coordinated_single_false_claim"
    assert report["report_sha256"]


def test_grid_reports_false_positives_where_f1_is_undefined() -> None:
    rendered = format_grid(run_sweep(recall_size=6, max_density=0.5, cases_per_cell=1))

    # An F1 of 0.00 in the clean row would read as "every detector failed" when
    # in fact there was nothing to detect. The leading 0.00 is the density label;
    # only the detector cells after it must avoid a score.
    clean_row = next(
        line for line in rendered.splitlines() if line.startswith("0.00 ")
    )
    cells = clean_row[9:]
    assert "fp=0" in cells
    assert "0.00" not in cells


def test_sweep_rejects_unusable_configurations() -> None:
    with pytest.raises(PoisonSweepError, match="recall_size"):
        run_sweep(recall_size=1)
    with pytest.raises(PoisonSweepError, match="max_density"):
        run_sweep(max_density=0.0)
    with pytest.raises(PoisonSweepError, match="cases_per_cell"):
        run_sweep(cases_per_cell=0)
    with pytest.raises(PoisonSweepError, match="unknown detector"):
        run_sweep(detectors=("clairvoyance",))
    with pytest.raises(PoisonSweepError, match="poisoned_count"):
        build_sweep_case(case_id="c", recall_size=4, poisoned_count=9)
    with pytest.raises(PoisonSweepError, match="one density"):
        score_detector(
            "anchored_contrast",
            (
                build_sweep_case(case_id="a", recall_size=4, poisoned_count=1),
                build_sweep_case(case_id="b", recall_size=4, poisoned_count=2),
            ),
            threshold=0.6,
        )
