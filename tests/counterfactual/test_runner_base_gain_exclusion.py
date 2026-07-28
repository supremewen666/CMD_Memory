"""Runner-level regression: a NaN identity-backbone gain excludes the case.

Exp21/22/23 all compute ``base_gain = rec()`` (the identity backbone) and then
``net(g) = g - base_gain``. If that single rollout times out, ``base_gain`` is
NaN, every arm's net is NaN, every ``net > threshold`` test is False, and the
seeded ``-1.0`` maximisation sentinels are never updated -- so the case gets
tallied as "not recovered". These tests pin the exclusion path that replaces
that mistally, and check the excluded detail rows are writable and distinct.
"""

from __future__ import annotations

import csv
import io
import math
import random
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cmd_audit.eval.writers import (
    best_scored_pair,
    nan_safe_max,
    recovery_case_outcomes,
)
from experiments import (
    run_experiment_21_operator_headroom as exp21,
    run_experiment_22_operator_transfer as exp22,
    run_experiment_23_item_headroom as exp23,
    run_experiment_23_item_transfer as item_transfer,
)
from experiments import analyze_significance

# Detail-table fieldnames each runner passes to write_csv_table. csv.DictWriter
# raises on keys outside fieldnames, so an excluded row must stay within them.
EXP21_FIELDS = {
    "case_id", "gold_label", "status", "excluded", "timeout_count",
    "base_gain", "single_net", "double_net", "param_net", "double_param_net",
    "richer_net", "single_op", "double_op", "param_op", "double_param_op",
    "richer_op", "headroom", "ec_test",
}
EXP22_ARMS = (
    "no_repair", "single_xfer", "comp_oracle", "comp_global", "comp_bm25",
    "comp_fp", "comp_fp_topN", "random_topN",
)
EXP22_FIELDS = (
    {"case_id", "gold_label", "status", "excluded", "timeout_count",
     "topn_cost", "topn_candidates", "random_topn_cost",
     "random_topn_candidates", "comp_oracle_op", "comp_fp_op",
     "comp_fp_topN_op", "random_topN_op"}
    | {f"{arm}_net" for arm in EXP22_ARMS}
    | {f"{arm}_rec" for arm in EXP22_ARMS}
)
EXP23_FIELDS = {
    "case_id", "gold_label", "status", "excluded", "timeout_count",
    "base_gain", "single_net", "double_net", "param_net", "double_param_net",
    "richer_net", "double_extra_over_single", "double_param_extra_over_single",
    "double_headroom_over_single", "double_param_headroom_over_single",
    "single_op", "double_op", "param_op", "double_param_op", "richer_op",
    "headroom", "data_floor_ok", "ec_test",
}
ITEM_TRANSFER_LEGACY_FIELDS = [
    "case_id",
    "gold_label",
    *[f"{arm}_net" for arm in item_transfer.ITEM_TRANSFER_ARMS],
    *[f"{arm}_rec" for arm in item_transfer.ITEM_TRANSFER_ARMS],
    "topn_cost",
    "topn_candidates",
    "random_topn_cost",
    "random_topn_candidates",
    "item_oracle_op",
    "item_fp_op",
    "item_fp_topN_op",
    "random_topN_op",
]


def _case():
    return SimpleNamespace(
        case_id="case-slow",
        perturbation_label="retrieval_error",
        extracted_memory=(),
        raw_events=(),
        gold_evidence=(),
    )


def _run_item_transfer(*, base_gain: float, oracle_gain: float | None = None):
    """Run one mocked item-transfer case without an LLM or dataset rebuild."""
    case = SimpleNamespace(
        case_id="item-case",
        perturbation_label="item_stale",
        query="Which item is current?",
        extracted_memory=(),
        raw_events=(),
        gold_answer="new",
        primary_baseline=SimpleNamespace(answer_score=0.0),
    )
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        bank_path = root / "bank.csv"
        out_path = root / "detail.csv"
        with bank_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                ["case_id", "gold_label", "richer_op", "single_op"],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "case_id": case.case_id,
                    "gold_label": case.perturbation_label,
                    "richer_op": "gp0:item_stale",
                    "single_op": "",
                }
            )

        gains = [base_gain]
        if oracle_gain is not None:
            gains.append(oracle_gain)
        stdout = io.StringIO()
        argv = [
            "run_experiment_23_item_transfer.py",
            "--cases",
            str(root / "unused.json"),
            "--operator-bank",
            str(bank_path),
            "--out",
            str(out_path),
            "--labels",
            case.perturbation_label,
        ]
        with (
            patch.object(sys, "argv", argv),
            patch.object(item_transfer, "OUT", root),
            patch.object(item_transfer, "load_probe_cases_v1", return_value=[case]),
            patch.object(
                item_transfer,
                "build_clients",
                return_value=(object(), object()),
            ),
            patch.object(item_transfer, "assert_g_eval_available"),
            patch.object(item_transfer, "build_answer_verifier", return_value=object()),
            patch.object(item_transfer, "_step_context", return_value="context"),
            patch.object(item_transfer, "_own_recovery", side_effect=gains),
            patch(
                "cmd_audit.harness._initial_mcts_context",
                return_value="initial-context",
            ),
            patch(
                "cmd_audit.harness._retrieved_memory_items",
                return_value=(),
            ),
            redirect_stdout(stdout),
        ):
            item_transfer.main()

        with out_path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        return rows, stdout.getvalue()


def _legacy_projection_bytes(row: dict[str, str]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, ITEM_TRANSFER_LEGACY_FIELDS)
    writer.writeheader()
    writer.writerow({field: row[field] for field in ITEM_TRANSFER_LEGACY_FIELDS})
    return buffer.getvalue().encode("utf-8")


class BaseGainExclusionTallyTest(unittest.TestCase):
    def test_nan_base_gain_returns_none_so_case_is_dropped(self) -> None:
        arm_scores = {"single": -1.0, "double": -1.0, "richer": -1.0}

        self.assertIsNone(
            recovery_case_outcomes(float("nan"), arm_scores, threshold=0.1)
        )

    def test_without_exclusion_the_case_would_read_as_not_recovered(self) -> None:
        # Reproduces the runner's own arithmetic on a NaN base_gain.
        base_gain = float("nan")
        single_best = -1.0
        for observed_score in (0.7, 1.0):
            net = observed_score - base_gain
            self.assertTrue(math.isnan(net))
            if net > single_best:
                single_best = net

        self.assertEqual(single_best, -1.0)
        self.assertFalse(single_best > 0.1)  # tallied as failure -- the bug

    def test_finite_base_gain_is_unaffected(self) -> None:
        outcomes = recovery_case_outcomes(
            0.2, {"single": 0.05, "richer": 0.4}, threshold=0.1
        )
        self.assertEqual(outcomes, {"single": False, "richer": True})

    def test_item_transfer_nan_base_excludes_every_tally(self) -> None:
        rows, output = _run_item_transfer(base_gain=float("nan"))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "base_gain_timeout")
        self.assertEqual(rows[0]["excluded"], "true")
        self.assertEqual(rows[0]["timeout_count"], "1")
        for arm in item_transfer.ITEM_TRANSFER_ARMS:
            self.assertEqual(rows[0][f"{arm}_net"], "")
            self.assertEqual(rows[0][f"{arm}_rec"], "false")
            summary_line = next(
                line for line in output.splitlines() if line.startswith(arm)
            )
            self.assertEqual(summary_line.split()[1:4], ["0", "0", "0.0000"])
        self.assertIn("EXCLUDED (identity-backbone rollout timed out): 1", output)

    def test_item_transfer_no_timeout_preserves_legacy_cells_byte_for_byte(self) -> None:
        rows, _output = _run_item_transfer(base_gain=0.2, oracle_gain=0.7)
        row = rows[0]

        # New metadata is load-bearing, while every pre-change cell retains its
        # exact serialized representation.
        self.assertEqual(
            (row["status"], row["excluded"], row["timeout_count"]),
            ("ok", "false", "0"),
        )
        expected = {
            "case_id": "item-case",
            "gold_label": "item_stale",
            **{f"{arm}_net": "0.0000" for arm in item_transfer.ITEM_TRANSFER_ARMS},
            **{f"{arm}_rec": "false" for arm in item_transfer.ITEM_TRANSFER_ARMS},
            "topn_cost": "0",
            "topn_candidates": "0",
            "random_topn_cost": "0",
            "random_topn_candidates": "0",
            "item_oracle_op": "gp0:item_stale",
            "item_fp_op": "",
            "item_fp_topN_op": "",
            "random_topN_op": "",
        }
        expected["item_oracle_net"] = "0.5000"
        expected["item_oracle_rec"] = "true"
        self.assertEqual(
            _legacy_projection_bytes(row),
            _legacy_projection_bytes(expected),
        )

    def test_item_transfer_arm_timeout_is_counted_not_base_excluded(self) -> None:
        rows, output = _run_item_transfer(
            base_gain=0.2,
            oracle_gain=float("nan"),
        )
        row = rows[0]

        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["excluded"], "false")
        self.assertEqual(row["timeout_count"], "1")
        self.assertEqual(row["item_oracle_net"], "nan")
        self.assertEqual(row["item_oracle_rec"], "false")
        oracle_summary = next(
            line for line in output.splitlines() if line.startswith("item_oracle")
        )
        self.assertEqual(oracle_summary.split()[1:4], ["0", "1", "0.0000"])
        self.assertNotIn(
            "EXCLUDED (identity-backbone rollout timed out):",
            output,
        )


class NanSafeSelectionTest(unittest.TestCase):
    def test_nan_safe_max_skips_timeouts(self) -> None:
        self.assertEqual(nan_safe_max(float("nan"), 0.3, 0.1), 0.3)
        self.assertTrue(math.isnan(nan_safe_max(float("nan"), float("nan"))))

    def test_best_pair_never_selects_a_timed_out_operator(self) -> None:
        score, op = best_scored_pair(
            [(float("nan"), "timed_out_op"), (0.2, "real_op"), (0.1, None)]
        )
        self.assertEqual((score, op), (0.2, "real_op"))

        # NaN first AND last: max(key=...) is order-dependent here, this is not.
        score, op = best_scored_pair(
            [(float("nan"), "a"), (0.5, "b"), (float("nan"), "c")]
        )
        self.assertEqual((score, op), (0.5, "b"))

    def test_exp21_wrappers_delegate_to_nan_safe_helpers(self) -> None:
        self.assertEqual(exp21._nan_safe_max(float("nan"), 0.4), 0.4)
        self.assertEqual(
            exp21._best_pair([(float("nan"), "x"), (0.3, "y")]), (0.3, "y")
        )

    def test_item_transfer_candidate_selection_is_order_independent(self) -> None:
        finite = (0.05, [(0, "finite")])
        timed_out = (float("nan"), [(0, "timed-out")])

        for candidates in ([timed_out, finite], [finite, timed_out]):
            score, op = item_transfer._best_topn_candidate(candidates)
            self.assertEqual(score, finite[0])
            self.assertEqual(op, finite[1])


class ExcludedDetailRowTest(unittest.TestCase):
    def test_exp21_excluded_row_is_distinct_and_writable(self) -> None:
        row = exp21._excluded_detail_row(_case())

        self.assertEqual(row["status"], "base_gain_timeout")
        self.assertEqual(row["excluded"], "true")
        self.assertEqual(row["timeout_count"], "1")
        self.assertEqual(row["base_gain"], "nan")
        self.assertNotEqual(row["base_gain"], "0.0000")
        self.assertEqual(row["single_net"], "")
        self.assertTrue(set(row).issubset(EXP21_FIELDS))

    def test_exp22_excluded_row_is_distinct_and_writable(self) -> None:
        row = exp22._excluded_detail_row(_case(), EXP22_ARMS)

        self.assertEqual(row["status"], "base_gain_timeout")
        self.assertEqual(row["excluded"], "true")
        self.assertEqual(row["timeout_count"], "1")
        for arm in EXP22_ARMS:
            self.assertEqual(row[f"{arm}_net"], "")
        self.assertTrue(set(row).issubset(EXP22_FIELDS))

    def test_exp23_excluded_row_is_distinct_and_writable(self) -> None:
        row = exp23._excluded_detail_row(_case())

        self.assertEqual(row["status"], "base_gain_timeout")
        self.assertEqual(row["excluded"], "true")
        self.assertEqual(row["timeout_count"], "1")
        self.assertEqual(row["base_gain"], "nan")
        self.assertTrue(set(row).issubset(EXP23_FIELDS))

    def test_item_transfer_excluded_row_matches_real_writer_fields(self) -> None:
        row = item_transfer._excluded_detail_row(_case())

        self.assertEqual(row["status"], "base_gain_timeout")
        self.assertEqual(row["excluded"], "true")
        self.assertEqual(row["timeout_count"], "1")
        self.assertTrue(
            set(row).issubset(set(item_transfer.ITEM_TRANSFER_DETAIL_FIELDS))
        )


class Exp23RecoveredPredicateTest(unittest.TestCase):
    def test_timed_out_arm_is_not_recovered(self) -> None:
        self.assertFalse(exp23._recovered(float("nan"), 0.1))
        self.assertFalse(exp23._recovered(0.05, 0.1))
        self.assertTrue(exp23._recovered(0.5, 0.1))

    def test_score_formatters_emit_nan_token_not_zero(self) -> None:
        self.assertEqual(exp23._fmt_score(float("nan"), object()), "nan")
        self.assertEqual(exp23._fmt_score(0.25, object()), "0.2500")
        self.assertEqual(exp23._fmt_score(0.25, None), "NA")
        self.assertEqual(exp23._fmt_optional_score(None), "NA")
        self.assertEqual(exp23._fmt_optional_score(float("nan")), "nan")


class SignificanceExclusionTest(unittest.TestCase):
    def test_excluded_and_nan_rows_do_not_enter_paired_denominator(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "detail.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    ["case_id", "arm", "recovered", "excluded"],
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {"case_id": "ok", "arm": "a", "recovered": "true",
                         "excluded": "false"},
                        {"case_id": "ok", "arm": "b", "recovered": "false",
                         "excluded": "false"},
                        {"case_id": "excluded", "arm": "a", "recovered": "false",
                         "excluded": "true"},
                        {"case_id": "excluded", "arm": "b", "recovered": "false",
                         "excluded": "true"},
                        {"case_id": "nan", "arm": "a", "recovered": "nan",
                         "excluded": "false"},
                        {"case_id": "nan", "arm": "b", "recovered": "true",
                         "excluded": "false"},
                    ]
                )

            paired = analyze_significance._load_paired(path)
            result = analyze_significance._compare(
                paired,
                "a",
                "b",
                random.Random(0),
            )

        self.assertEqual(result["n_paired"], "1")
        self.assertEqual(result["rate_a"], "1.0000")
        self.assertEqual(result["rate_b"], "0.0000")


if __name__ == "__main__":
    unittest.main()
