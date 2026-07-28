"""The Exp21/22 analyzers must drop runner-excluded rows, not re-count them.

The runners exclude cases whose identity-backbone rollout timed out (no measured
net gain). If the post-processing analyzers kept those rows, each would be read
as "not recovered" and the denominator would grow, reintroducing the downward
bias one layer further out.
"""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

EXP21_FIELDS = [
    "case_id", "gold_label", "status", "excluded", "timeout_count", "base_gain",
    "single_net", "double_net", "param_net", "double_param_net", "richer_net",
    "single_op", "double_op", "param_op", "double_param_op", "richer_op",
    "headroom", "ec_test",
]
EXP22_ARMS = [
    "no_repair", "single_xfer", "comp_oracle", "comp_global", "comp_bm25",
    "comp_fp", "comp_fp_topN", "random_topN",
]
EXP22_FIELDS = (
    ["case_id", "gold_label", "status", "excluded", "timeout_count"]
    + [f"{arm}_net" for arm in EXP22_ARMS]
    + [f"{arm}_rec" for arm in EXP22_ARMS]
    + ["topn_cost", "topn_candidates", "random_topn_cost",
       "random_topn_candidates", "comp_oracle_op", "comp_fp_op",
       "comp_fp_topN_op", "random_topN_op"]
)


def _write(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fields)
        writer.writeheader()
        writer.writerows(rows)


def _run(module: str, csv_path: Path) -> str:
    proc = subprocess.run(
        [sys.executable, "-m", module, "--csv", str(csv_path)],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _exp21_row(case_id: str, *, excluded: bool) -> dict[str, str]:
    row = {field: "" for field in EXP21_FIELDS}
    row.update(
        case_id=case_id,
        gold_label="retrieval_error",
        status="base_gain_timeout" if excluded else "ok",
        excluded="true" if excluded else "false",
        timeout_count="1" if excluded else "0",
        base_gain="nan" if excluded else "0.0000",
        headroom="false",
    )
    if not excluded:
        for col in ("single_net", "double_net", "param_net",
                    "double_param_net", "richer_net"):
            row[col] = "0.5000"
    return row


def _exp22_row(case_id: str, *, excluded: bool) -> dict[str, str]:
    row = {field: "" for field in EXP22_FIELDS}
    row.update(
        case_id=case_id,
        gold_label="retrieval_error",
        status="base_gain_timeout" if excluded else "ok",
        excluded="true" if excluded else "false",
        timeout_count="1" if excluded else "0",
    )
    for arm in EXP22_ARMS:
        row[f"{arm}_net"] = "" if excluded else "0.5000"
        row[f"{arm}_rec"] = "false" if excluded else "true"
    for col in ("topn_cost", "topn_candidates", "random_topn_cost",
                "random_topn_candidates"):
        row[col] = "0"
    return row


def test_exp21_analyzer_drops_excluded_rows(tmp_path: Path) -> None:
    path = tmp_path / "operator_headroom_detail.csv"
    _write(
        path,
        EXP21_FIELDS,
        [
            _exp21_row("ok-1", excluded=False),
            _exp21_row("ok-2", excluded=False),
            _exp21_row("timed-out", excluded=True),
        ],
    )

    output = _run("experiments.analyze_operator_headroom", path)

    assert "loaded 2 residual cases" in output
    assert "1 excluded: base_gain timeout" in output
    # richer recovered in both retained cases -> rate 1.000, not 0.667.
    assert "richer          1.000" in output


def test_exp22_analyzer_drops_excluded_rows(tmp_path: Path) -> None:
    path = tmp_path / "operator_transfer_detail.csv"
    _write(
        path,
        EXP22_FIELDS,
        [
            _exp22_row("ok-1", excluded=False),
            _exp22_row("timed-out", excluded=True),
        ],
    )

    output = _run("experiments.analyze_operator_transfer", path)

    assert "loaded 1 cases" in output
    assert "1 excluded: base_gain timeout" in output
    assert "comp_fp_topN        1/1" in output


def test_analyzers_still_read_pre_change_csvs(tmp_path: Path) -> None:
    """Old artifacts have no status/excluded/timeout_count columns."""
    legacy_21 = [f for f in EXP21_FIELDS
                 if f not in ("status", "excluded", "timeout_count")]
    row = _exp21_row("ok-1", excluded=False)
    path21 = tmp_path / "legacy_headroom.csv"
    _write(path21, legacy_21, [{k: row[k] for k in legacy_21}])
    assert "loaded 1 residual cases" in _run(
        "experiments.analyze_operator_headroom", path21
    )

    legacy_22 = [f for f in EXP22_FIELDS
                 if f not in ("status", "excluded", "timeout_count")]
    row22 = _exp22_row("ok-1", excluded=False)
    path22 = tmp_path / "legacy_transfer.csv"
    _write(path22, legacy_22, [{k: row22[k] for k in legacy_22}])
    assert "loaded 1 cases" in _run(
        "experiments.analyze_operator_transfer", path22
    )


def test_exp22_rec_treats_nan_net_as_not_recovered() -> None:
    from experiments.analyze_operator_transfer import _rec

    assert _rec({"comp_fp_net": "nan"}, "comp_fp") is False
    assert _rec({"comp_fp_net": "0.5000"}, "comp_fp") is True
    assert _rec({"comp_fp_net": ""}, "comp_fp") is False


def test_exp21_net_treats_nan_as_missing() -> None:
    """A `nan` cell must not parse to a float that could clear the threshold."""
    import experiments.analyze_operator_headroom as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert '"nan"' in source, "analyzer must special-case the nan token"


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__]))
