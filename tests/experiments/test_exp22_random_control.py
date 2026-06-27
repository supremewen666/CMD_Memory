import csv
import subprocess
import sys

from experiments.run_experiment_22_operator_transfer import (
    _distinct_shapes,
    _random_topn_shapes,
)


def test_distinct_shapes_preserves_order_and_drops_empty() -> None:
    shape_a = ((0, "retrieval_error"),)
    shape_b = ((1, "injection_error"),)

    assert _distinct_shapes(("", shape_a, shape_a, shape_b, ())) == [
        shape_a,
        shape_b,
    ]


def test_random_topn_shapes_is_deterministic_per_case_and_seed() -> None:
    shapes = [
        ((0, "retrieval_error"),),
        ((1, "injection_error"),),
        ((2, "safety_error"),),
        ((0, "retrieval_error"),),
    ]

    first = _random_topn_shapes(shapes, case_id="case-1", seed=22, topn=2)
    second = _random_topn_shapes(shapes, case_id="case-1", seed=22, topn=2)
    other_case = _random_topn_shapes(shapes, case_id="case-2", seed=22, topn=2)

    assert first == second
    assert len(first) == 2
    assert first != other_case


def test_transfer_analyzer_accepts_multiple_csvs(tmp_path) -> None:
    fieldnames = [
        "case_id",
        "comp_fp_topN_rec",
        "random_topN_rec",
        "single_xfer_rec",
        "comp_oracle_rec",
    ]
    rows = [
        {
            "case_id": "c1",
            "comp_fp_topN_rec": "true",
            "random_topN_rec": "false",
            "single_xfer_rec": "false",
            "comp_oracle_rec": "true",
        },
        {
            "case_id": "c2",
            "comp_fp_topN_rec": "true",
            "random_topN_rec": "true",
            "single_xfer_rec": "false",
            "comp_oracle_rec": "true",
        },
    ]
    paths = []
    for idx in (1, 2):
        path = tmp_path / f"run{idx}.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        paths.append(str(path))

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "experiments.analyze_operator_transfer",
            "--csv",
            *paths,
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "fingerprint topN vs same-budget random operator topN" in result.stdout
    assert "Multi-run stability summary" in result.stdout
    assert "beats random_topN in 2/2 run(s)" in result.stdout
