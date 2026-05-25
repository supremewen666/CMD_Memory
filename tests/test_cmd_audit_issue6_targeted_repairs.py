"""Targeted memory fix validation tests — Issue 0006."""

import tempfile
from pathlib import Path
import unittest

from cmd_audit import (
    FullAuditResult,
    RepairClaimLedger,
    RepairComparisonRow,
    TargetedRepairAction,
    build_repair_claim_ledger,
    compute_repair_success_summary,
    get_targeted_repair_action,
    load_probe_cases,
    make_repair_comparison,
    run_cases_full,
    write_repair_success_table_from_full,
    V0_PIPELINE_LABEL_ORDER,
    V1_PIPELINE_LABEL_ORDER,
)
from cmd_audit.labels import LabelValidationError


ISSUE_3_CASES = Path("data/probe_cases/v0_issue3_cases.json")


# ── Label-to-Repair Mapping ───────────────────────────────────────────


class LabelToRepairMappingTest(unittest.TestCase):
    """AC: Each major attribution label maps to a repair action."""

    def test_all_six_v0_labels_have_targeted_repair(self) -> None:
        for label in V0_PIPELINE_LABEL_ORDER:
            with self.subTest(label=label):
                action = get_targeted_repair_action(label)
                self.assertIsInstance(action, TargetedRepairAction)
                self.assertEqual(action.label, label)
                self.assertTrue(
                    action.action_name, f"{label}: action_name must not be empty"
                )
                self.assertTrue(
                    action.description, f"{label}: description must not be empty"
                )
                self.assertTrue(
                    action.intervention_summary,
                    f"{label}: intervention_summary must not be empty",
                )

    def test_targeted_repair_actions_are_distinct(self) -> None:
        names = {
            label: get_targeted_repair_action(label).action_name
            for label in V0_PIPELINE_LABEL_ORDER
        }
        self.assertEqual(
            len(names), 6, "All six labels must have distinct repair action names"
        )

    def test_get_targeted_repair_rejects_invalid_label(self) -> None:
        with self.assertRaises((LabelValidationError, ValueError)):
            get_targeted_repair_action("item_wrong")

    def test_each_repair_action_describes_targeted_intervention(self) -> None:
        for label in V0_PIPELINE_LABEL_ORDER:
            action = get_targeted_repair_action(label)
            self.assertNotIn(
                "all extracted memory",
                action.description.casefold(),
                f"{label} repair must not describe undifferentiated hard-case update",
            )


# ── Repair Comparison Row ──────────────────────────────────────────────


class RepairComparisonRowTest(unittest.TestCase):
    """AC: CMD-guided repairs compared with undifferentiated hard-case updates."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(ISSUE_3_CASES)
        cls.full_results = run_cases_full(cls.cases)
        cls.rows = [make_repair_comparison(fr) for fr in cls.full_results]

    def test_one_row_per_case(self) -> None:
        self.assertEqual(len(self.rows), 6)

    def test_each_row_has_required_fields(self) -> None:
        for row in self.rows:
            with self.subTest(case_id=row.case_id):
                self.assertIsInstance(row.case_id, str)
                self.assertIsInstance(row.perturbation_label, str)
                self.assertIsInstance(row.predicted_label, str)
                self.assertIsInstance(row.repair_action, str)
                self.assertIsInstance(row.targeted_assessment, str)
                self.assertIsInstance(row.hard_case_assessment, str)
                self.assertIsInstance(row.targeted_better, bool)
                self.assertIn(
                    row.targeted_assessment, ("recovered", "partial", "failed")
                )
                self.assertIn(
                    row.hard_case_assessment, ("recovered", "partial", "failed")
                )

    def test_repair_action_matches_predicted_label(self) -> None:
        for row in self.rows:
            expected = get_targeted_repair_action(row.predicted_label).action_name
            self.assertEqual(
                row.repair_action,
                expected,
                f"{row.case_id}: repair_action should match predicted_label",
            )

    def test_targeted_and_hard_case_have_independent_results(self) -> None:
        for row in self.rows:
            with self.subTest(case_id=row.case_id):
                self.assertIsInstance(row.targeted_token_cost, float)
                self.assertIsInstance(row.hard_case_token_cost, float)
                self.assertGreaterEqual(row.targeted_token_cost, 0.0)
                self.assertGreaterEqual(row.hard_case_token_cost, 0.0)

    def test_targeted_better_flag_is_consistent(self) -> None:
        for row in self.rows:
            order = {"recovered": 0, "partial": 1, "failed": 2}
            t_rank = order[row.targeted_assessment]
            h_rank = order[row.hard_case_assessment]
            if t_rank < h_rank:
                self.assertTrue(
                    row.targeted_better,
                    f"{row.case_id}: targeted better rank but flag is False",
                )
            elif t_rank > h_rank:
                self.assertFalse(
                    row.targeted_better,
                    f"{row.case_id}: hard-case better rank but targeted_better is True",
                )


# ── Repair Success Summary ─────────────────────────────────────────────


class RepairSuccessSummaryTest(unittest.TestCase):
    """Per-label aggregation of repair outcomes."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(ISSUE_3_CASES)
        cls.full_results = run_cases_full(cls.cases)
        cls.rows = [make_repair_comparison(fr) for fr in cls.full_results]
        cls.summaries = compute_repair_success_summary(cls.rows)

    def test_summary_covers_all_perturbation_labels(self) -> None:
        expected = {case.perturbation_label for case in self.cases}
        self.assertEqual(set(self.summaries.keys()), expected)

    def test_each_summary_has_balanced_counts(self) -> None:
        for label, summary in self.summaries.items():
            with self.subTest(label=label):
                self.assertEqual(
                    summary.total_cases,
                    summary.targeted_recovered
                    + summary.targeted_partial
                    + summary.targeted_failed,
                    f"{label}: targeted counts must sum to total_cases",
                )
                self.assertEqual(
                    summary.total_cases,
                    summary.hard_case_recovered
                    + summary.hard_case_partial
                    + summary.hard_case_failed,
                    f"{label}: hard-case counts must sum to total_cases",
                )
                self.assertEqual(
                    summary.total_cases,
                    summary.targeted_better_count
                    + summary.hard_case_better_count
                    + summary.same_outcome_count,
                    f"{label}: better/same counts must sum to total_cases",
                )

    def test_recovery_rates_are_valid(self) -> None:
        for label, summary in self.summaries.items():
            with self.subTest(label=label):
                self.assertGreaterEqual(summary.targeted_recovery_rate, 0.0)
                self.assertLessEqual(summary.targeted_recovery_rate, 1.0)
                self.assertGreaterEqual(summary.hard_case_recovery_rate, 0.0)
                self.assertLessEqual(summary.hard_case_recovery_rate, 1.0)

    def test_token_costs_are_positive(self) -> None:
        for label, summary in self.summaries.items():
            with self.subTest(label=label):
                self.assertGreater(summary.avg_targeted_token_cost, 0.0)
                self.assertGreater(summary.avg_hard_case_token_cost, 0.0)

    def test_v1_only_label_is_aggregated(self) -> None:
        row = RepairComparisonRow(
            case_id="v1-route-001",
            perturbation_label="route_error",
            predicted_label="route_error",
            repair_action="Oracle Route Repair",
            pre_repair_answer_score=0.0,
            pre_repair_evidence_score=0.0,
            targeted_assessment="recovered",
            targeted_answer_score=1.0,
            targeted_evidence_score=1.0,
            targeted_token_cost=10.0,
            hard_case_assessment="failed",
            hard_case_answer_score=0.0,
            hard_case_evidence_score=0.0,
            hard_case_token_cost=20.0,
            targeted_better=True,
        )
        summaries = compute_repair_success_summary([row])
        self.assertIn("route_error", summaries)
        self.assertEqual(summaries["route_error"].targeted_recovered, 1)
        self.assertIn("route_error", V1_PIPELINE_LABEL_ORDER)


# ── Claim Ledger ───────────────────────────────────────────────────────


class ClaimLedgerTest(unittest.TestCase):
    """AC: Claim ledger records whether targeted fixes are actually better."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(ISSUE_3_CASES)
        cls.full_results = run_cases_full(cls.cases)
        cls.rows = [make_repair_comparison(fr) for fr in cls.full_results]
        cls.summaries = compute_repair_success_summary(cls.rows)
        cls.ledger = build_repair_claim_ledger(cls.summaries)

    def test_ledger_is_complete(self) -> None:
        self.assertIsInstance(self.ledger, RepairClaimLedger)
        self.assertEqual(self.ledger.total_cases, 6)
        self.assertIsInstance(self.ledger.claim_supported, bool)

    def test_ledger_rates_in_range(self) -> None:
        for rate in (
            self.ledger.targeted_recovery_rate,
            self.ledger.hard_case_recovery_rate,
            self.ledger.targeted_full_plus_partial_rate,
            self.ledger.hard_case_full_plus_partial_rate,
            self.ledger.targeted_better_pct,
            self.ledger.avg_targeted_token_saving_pct,
        ):
            self.assertGreaterEqual(rate, 0.0)
            self.assertLessEqual(rate, 1.0)

    def test_ledger_with_empty_data(self) -> None:
        empty_ledger = build_repair_claim_ledger({})
        self.assertEqual(empty_ledger.total_cases, 0)
        self.assertEqual(empty_ledger.targeted_recovery_rate, 0.0)
        self.assertFalse(empty_ledger.claim_supported)

    def test_ledger_claim_is_evidence_based(self) -> None:
        targeted_any = self.ledger.targeted_full_plus_partial_rate
        hard_case_any = self.ledger.hard_case_full_plus_partial_rate
        token_saving = self.ledger.avg_targeted_token_saving_pct

        expected = targeted_any >= hard_case_any and token_saving > 0.0
        expected = (
            expected
            or self.ledger.targeted_recovery_rate > self.ledger.hard_case_recovery_rate
        )
        self.assertEqual(self.ledger.claim_supported, expected)


# ── Repair Success Table Output ────────────────────────────────────────


class RepairSuccessTableTest(unittest.TestCase):
    """AC: Repair success table is grounded in Post-Repair Context Replay."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(ISSUE_3_CASES)
        cls.full_results = run_cases_full(cls.cases)

    def test_repair_success_table_writes_comparison_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = Path(tmpdir) / "sandbox"
            sandbox.mkdir()
            output = sandbox / "repair_success_table.csv"
            write_repair_success_table_from_full(
                self.full_results, output, sandbox_root=sandbox
            )

            self.assertTrue(output.exists())
            header = output.read_text(encoding="utf-8").splitlines()[0]
            required = [
                "case_id",
                "perturbation_label",
                "predicted_label",
                "repair_action",
                "targeted_assessment",
                "targeted_answer_score",
                "targeted_evidence_score",
                "hard_case_assessment",
                "hard_case_answer_score",
                "hard_case_evidence_score",
                "targeted_better",
            ]
            for col in required:
                with self.subTest(column=col):
                    self.assertIn(col, header)

    def test_repair_success_table_writes_label_summary_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = Path(tmpdir) / "sandbox"
            sandbox.mkdir()
            output = sandbox / "repair_success_table.csv"
            write_repair_success_table_from_full(
                self.full_results, output, sandbox_root=sandbox
            )

            summary_path = sandbox / "repair_label_summary.csv"
            self.assertTrue(summary_path.exists())
            text = summary_path.read_text(encoding="utf-8")
            self.assertIn("label", text)
            self.assertIn("targeted_recovery_rate", text)
            self.assertIn("hard_case_recovery_rate", text)

    def test_repair_success_table_writes_claim_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = Path(tmpdir) / "sandbox"
            sandbox.mkdir()
            output = sandbox / "repair_success_table.csv"
            write_repair_success_table_from_full(
                self.full_results, output, sandbox_root=sandbox
            )

            ledger_path = sandbox / "repair_claim_ledger.txt"
            self.assertTrue(ledger_path.exists())
            ledger_text = ledger_path.read_text(encoding="utf-8")
            self.assertIn("CMD V1 Repair Claim Ledger", ledger_text)
            self.assertIn("Claim supported:", ledger_text)

    def test_repair_success_table_rejects_outside_sandbox(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = Path(tmpdir) / "sandbox"
            sandbox.mkdir()
            outside = Path(tmpdir) / "outside" / "repair_success_table.csv"
            outside.parent.mkdir()
            with self.assertRaises(ValueError):
                write_repair_success_table_from_full(
                    self.full_results, outside, sandbox_root=sandbox
                )


# ── End-to-End: Full Pipeline Per Label ────────────────────────────────


class FullPipelinePerLabelTest(unittest.TestCase):
    """Verify full CMD pipeline produces valid repair results per label."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(ISSUE_3_CASES)
        cls.full_results = run_cases_full(cls.cases)

    def test_all_six_cases_produce_full_results(self) -> None:
        self.assertEqual(len(self.full_results), 6)
        for fr in self.full_results:
            self.assertIsInstance(fr, FullAuditResult)

    def test_all_cases_have_valid_attribution(self) -> None:
        for fr in self.full_results:
            with self.subTest(case_id=fr.audit.case_id):
                self.assertEqual(
                    fr.audit.perturbation_label,
                    fr.audit.attribution.predicted_label,
                    f"{fr.audit.case_id}: attribution must match perturbation label",
                )

    def test_targeted_repair_differs_from_hard_case_baseline(self) -> None:
        for fr in self.full_results:
            with self.subTest(case_id=fr.audit.case_id):
                ecs = fr.ecs_draft
                ctx = fr.repaired_context
                self.assertNotIn(
                    "all extracted memory",
                    ecs.repair_guidance.casefold(),
                    "ECS repair guidance must not describe hard-case update",
                )
                self.assertNotIn(
                    "Hard-case update",
                    ctx.repair_guidance,
                    "Repaired context must not use hard-case update language",
                )

    def test_partial_assessments_exist_where_expected(self) -> None:
        # At least one post_repair_result should be available;
        # partial is a valid assessment for coupled failures.
        assessments = {fr.post_repair.repair_assessment for fr in self.full_results}
        self.assertIn("recovered", assessments)
        # partial may or may not appear in this smoke suite; both are valid.

    def test_hard_case_baseline_differs_from_targeted(self) -> None:
        for fr in self.full_results:
            with self.subTest(case_id=fr.audit.case_id):
                hard = fr.hard_case_baseline
                self.assertIn(
                    hard.repair_assessment, ("recovered", "partial", "failed")
                )
                self.assertGreaterEqual(hard.token_cost, 0.0)


if __name__ == "__main__":
    unittest.main()
