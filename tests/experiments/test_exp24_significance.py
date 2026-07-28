"""Gate 2 (Exp24) significance analysis.

The Exp24 verdict decides whether the paper claims per-generation evolution
(step-layer only) or falls back to Exp23's item-layer finding, so the analysis
has to be honest about three things: excluded cases are unmeasured rather than
failed, an unrun control arm is unavailable rather than beaten, and a single
seed never settles the gate.
"""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

from experiments import analyze_significance as sig

ROOT = Path(__file__).resolve().parent.parent.parent

exp25_cov = sig._exp24_coverage

FIELDS = [
    "case_index",
    "generation_bin",
    "case_id",
    "excluded",
    "timeout_count",
    "recovered",
    "recovery_source",
    "library_size_before",
    "total_rollouts",
    "fixed_recovered",
    "random_recovered",
]


def _write(path: Path, rows: list[dict[str, str]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _row(idx: int, bin_index: int, **over) -> dict[str, str]:
    row = {
        "case_index": str(idx),
        "generation_bin": str(bin_index),
        "case_id": f"case-{idx}",
        "excluded": "false",
        "timeout_count": "0",
        "recovered": "true",
        "recovery_source": "library",
        "library_size_before": str(bin_index * 3),
        "total_rollouts": "2",
        "fixed_recovered": "false",
        "random_recovered": "false",
    }
    row.update(over)
    return row


def _climbing(n_bins: int = 4, per_bin: int = 10) -> list[dict[str, str]]:
    """Recovery rises from 20% to 80% across bins."""
    rows: list[dict[str, str]] = []
    idx = 1
    for b in range(1, n_bins + 1):
        hits = int(per_bin * (0.2 * b))
        for k in range(per_bin):
            rows.append(
                _row(idx, b, recovered="true" if k < hits else "false")
            )
            idx += 1
    return rows


def _flat(n_bins: int = 4, per_bin: int = 10) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    idx = 1
    for b in range(1, n_bins + 1):
        for k in range(per_bin):
            rows.append(_row(idx, b, recovered="true" if k < 5 else "false"))
            idx += 1
    return rows


# ── row loading / exclusion ─────────────────────────────────────────────


def test_excluded_rows_are_dropped_before_any_statistic(tmp_path) -> None:
    """A NaN identity baseline makes every arm's net NaN, so the case is
    unmeasured. Counting it as a failure is the exact downward bias the NaN
    sentinel exists to remove."""
    path = _write(
        tmp_path / "operator_trajectory_detail.csv",
        [
            _row(1, 1, recovered="true"),
            _row(
                2,
                1,
                excluded="true",
                recovered="false",
                recovery_source="excluded",
                timeout_count="1",
            ),
        ],
    )

    rows = sig._exp24_rows(path)

    assert len(rows) == 1
    assert rows[0]["case_id"] == "case-1"


# ── arm availability ────────────────────────────────────────────────────


def test_blank_control_column_reports_unavailable_not_zero() -> None:
    """--controls off leaves blanks. Reading blank as False would invent a
    control the live arm trivially beats."""
    rows = [_row(1, 1, fixed_recovered=""), _row(2, 1, fixed_recovered="")]

    assert sig._exp24_arm_flags(rows, "fixed_recovered") is None


def test_partially_blank_control_column_is_also_unavailable() -> None:
    rows = [_row(1, 1, fixed_recovered="true"), _row(2, 1, fixed_recovered="")]

    assert sig._exp24_arm_flags(rows, "fixed_recovered") is None


def test_coverage_of_one_means_random_exhausted_the_library() -> None:
    """budget/pool >= 1 makes the "random" arm an exhaustive search over the
    library, which strictly dominates any ranked top-N. Such a comparison is
    degenerate, not same-strength."""
    assert exp25_cov({"random_coverage": "1.0000"}) == 1.0
    assert exp25_cov({"random_coverage": "0.1000"}) == 0.1


def test_missing_coverage_is_treated_as_non_degenerate() -> None:
    """Older CSVs predate the column. Defaulting to 1.0 would silently discard
    every row; defaulting to 0.0 keeps them in the comparison."""
    assert exp25_cov({}) == 0.0
    assert exp25_cov({"random_coverage": ""}) == 0.0
    assert exp25_cov({"random_coverage": "n/a"}) == 0.0


def test_pool_coverage_formula() -> None:
    from experiments import run_experiment_24_operator_trajectory as exp24

    # Library grows from empty, so early-stream pools are smaller than budget.
    assert exp24._random_coverage(0, 5) == 0.0
    assert exp24._random_coverage(2, 5) == 1.0
    assert exp24._random_coverage(5, 5) == 1.0
    assert exp24._random_coverage(50, 5) == 0.1


def test_populated_control_column_parses_to_flags() -> None:
    rows = [
        _row(1, 1, fixed_recovered="true"),
        _row(2, 1, fixed_recovered="false"),
    ]

    assert sig._exp24_arm_flags(rows, "fixed_recovered") == [True, False]


# ── trend detection ─────────────────────────────────────────────────────


def test_climbing_stream_is_detected_as_a_rising_trend() -> None:
    rows = _climbing()
    bins: dict[int, list[bool]] = {}
    for row in rows:
        bins.setdefault(int(row["generation_bin"]), []).append(
            row["recovered"] == "true"
        )
    counts = [(sum(bins[b]), len(bins[b])) for b in sorted(bins)]

    z, p = sig._cochran_armitage(counts)

    assert z > 0
    assert p < 0.05


def test_flat_stream_yields_no_trend() -> None:
    rows = _flat()
    bins: dict[int, list[bool]] = {}
    for row in rows:
        bins.setdefault(int(row["generation_bin"]), []).append(
            row["recovered"] == "true"
        )
    counts = [(sum(bins[b]), len(bins[b])) for b in sorted(bins)]

    z, p = sig._cochran_armitage(counts)

    assert abs(z) < 1.0
    assert p > 0.05


# ── cost axis ───────────────────────────────────────────────────────────


def test_cost_axis_reports_falling_rollouts() -> None:
    """Flat recovery with falling cost is the 'warm-up reuse' finding, not a
    null result -- the analysis must be able to see it."""
    rows = [
        _row(1, 1, total_rollouts="6"),
        _row(2, 1, total_rollouts="6"),
        _row(3, 2, total_rollouts="2"),
        _row(4, 2, total_rollouts="2"),
    ]
    bins = {1: [True, True], 2: [True, True]}

    first, last = sig._exp24_cost_ends(rows, [1, 2], bins)

    assert first == 6.0
    assert last == 2.0


def test_cost_axis_needs_two_bins() -> None:
    rows = [_row(1, 1)]
    assert sig._exp24_cost_ends(rows, [1], {1: [True]}) == (None, None)


# ── correlation helper ──────────────────────────────────────────────────


def test_pearson_returns_none_for_constant_series() -> None:
    assert sig._pearson([1.0, 1.0, 1.0], [0.1, 0.2, 0.3]) is None


def test_pearson_detects_positive_association() -> None:
    corr = sig._pearson([1.0, 2.0, 3.0], [0.2, 0.4, 0.6])
    assert corr is not None and corr > 0.99


# ── verdict gating ──────────────────────────────────────────────────────


def test_verdict_refuses_to_settle_on_fewer_than_three_seeds(capsys) -> None:
    sig._exp24_verdict(["up"], 1)

    out = capsys.readouterr().out
    assert "NOT SETTLED" in out
    assert ">=3 required" in out


def test_verdict_flags_disagreeing_seeds(capsys) -> None:
    sig._exp24_verdict(["up", "down", "up"], 3)

    out = capsys.readouterr().out
    assert "DISAGREE" in out


def test_verdict_confirms_agreeing_seeds(capsys) -> None:
    sig._exp24_verdict(["up", "up", "up"], 3)

    out = capsys.readouterr().out
    assert "agree in direction" in out
    assert "DISAGREE" not in out


# ── end-to-end resilience ───────────────────────────────────────────────


def test_analyzer_reports_all_sections_and_survives_missing_exp24() -> None:
    """analyze_significance runs as a whole. A missing or empty Exp24 CSV must
    not stop the C4/C5/C8/C13/Exp18 sections from being reported."""
    result = subprocess.run(
        [sys.executable, "-m", "experiments.analyze_significance"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert result.returncode == 0, result.stderr[-2000:]
    assert "Gate 2 (Exp24)" in result.stdout
    assert "C7 (Exp18)" in result.stdout
