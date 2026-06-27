from __future__ import annotations

import csv
from pathlib import Path
from types import SimpleNamespace

from cmd_audit.harness import write_comparison_metrics_table


def _audit(recovery_gain: float | None, *, provenance_edges: tuple = ()):
    attribution = None
    if recovery_gain is not None:
        attribution = SimpleNamespace(recovery_gain=recovery_gain)
    comparator = SimpleNamespace(
        comparator_name="random_label",
        cost_per_diagnosis=0.01,
    )
    return SimpleNamespace(
        attribution=attribution,
        replays=[SimpleNamespace(provenance_edges=provenance_edges)],
        diagnosis_cost=1.5,
        baseline_suite=SimpleNamespace(comparator_results=(comparator,)),
    )


def test_comparison_metrics_table_reports_operator_recovery_not_macro_f1(
    tmp_path: Path,
) -> None:
    path = tmp_path / "comparison_metrics.csv"
    results = [
        _audit(0.5, provenance_edges=("edge-1",)),
        _audit(0.0),
        _audit(None),
    ]

    write_comparison_metrics_table(results, path)

    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert "macro_f1" not in rows[0]
    assert "attribution_accuracy" not in rows[0]
    assert "top2_accuracy" not in rows[0]

    cmd_row = next(row for row in rows if row["system_name"] == "CMD-Audit")
    assert cmd_row["cases"] == "3"
    assert cmd_row["triggered_cases"] == "2"
    assert cmd_row["positive_recovery_rate"] == "0.500"
    assert cmd_row["mean_recovery_gain"] == "0.250"
    assert cmd_row["provenance_completeness"] == "0.333"
