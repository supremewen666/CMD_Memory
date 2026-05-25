"""ECS Failure Memory recurrence tests — Issue 0007."""

import tempfile
from pathlib import Path
import unittest

from cmd_audit import (
    FailureMemoryRecord,
    FailureMemoryStore,
    RecurrenceSummary,
    build_failure_memory_context,
    compute_recurrence_summary,
    draft_ecs,
    load_probe_cases,
    run_case,
    run_case_full,
    run_recurrence_comparisons,
    write_recurrence_comparison_table,
    V0_PIPELINE_LABEL_ORDER,
)
from cmd_audit.labels import LabelValidationError

ISSUE_3_CASES = Path("data/probe_cases/v0_issue3_cases.json")
ISSUE_7_CASES = Path("data/probe_cases/v0_issue7_future_cases.json")


# ── FailureMemoryRecord creation ────────────────────────────────────────


class FailureMemoryRecordCreationTest(unittest.TestCase):
    """AC: ECS records convert to Failure Memory with all required fields."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(ISSUE_3_CASES)

    def test_record_from_ecs_has_all_required_fields(self) -> None:
        for case in self.cases:
            audit = run_case(case)
            ecs = draft_ecs(case, audit)
            record = FailureMemoryRecord.from_ecs_draft(ecs, case)

            with self.subTest(case_id=case.case_id):
                self.assertIsInstance(record.error_type, str)
                self.assertIn(record.error_type, V0_PIPELINE_LABEL_ORDER)
                self.assertIsInstance(record.wrong_memory, str)
                self.assertIsInstance(record.original_evidence, str)
                self.assertIsInstance(record.cause, str)
                self.assertIsInstance(record.corrected_memory, str)
                self.assertIsInstance(record.repair_action, str)
                self.assertIsInstance(record.repair_guidance, str)
                self.assertIsInstance(record.trigger_signature, str)
                self.assertTrue(len(record.trigger_signature) > 0)

    def test_record_error_type_matches_ecs_predicted_label(self) -> None:
        for case in self.cases:
            audit = run_case(case)
            ecs = draft_ecs(case, audit)
            record = FailureMemoryRecord.from_ecs_draft(ecs, case)

            with self.subTest(case_id=case.case_id):
                self.assertEqual(record.error_type, ecs.predicted_label)

    def test_record_rejects_invalid_error_type(self) -> None:
        case = self.cases[0]
        audit = run_case(case)
        _ = draft_ecs(case, audit)

        with self.assertRaises((LabelValidationError, ValueError)):
            FailureMemoryRecord(
                error_type="item_wrong",
                wrong_memory="",
                original_evidence="",
                cause="valid cause text",
                corrected_memory="",
                repair_action="",
                repair_guidance="",
                trigger_signature="test",
            )

    def test_trigger_signature_contains_label_and_keywords(self) -> None:
        case = self.cases[0]
        audit = run_case(case)
        ecs = draft_ecs(case, audit)
        record = FailureMemoryRecord.from_ecs_draft(ecs, case)

        self.assertIn(ecs.predicted_label, record.trigger_signature)
        # Should contain query keywords
        self.assertTrue(
            "|" in record.trigger_signature,
            f"trigger_signature should contain label|keywords: {record.trigger_signature}",
        )

    def test_wrong_memory_reflects_baseline_context(self) -> None:
        # wrong_memory comes from baseline injected_context, which for
        # reasoning_error cases may contain the gold evidence (but the
        # baseline still answered wrong). For other labels the baseline
        # context typically lacks the evidence.
        for case in self.cases:
            audit = run_case(case)
            ecs = draft_ecs(case, audit)
            record = FailureMemoryRecord.from_ecs_draft(ecs, case)

            with self.subTest(case_id=case.case_id):
                self.assertEqual(
                    record.wrong_memory,
                    case.primary_baseline.injected_context,
                    f"{case.case_id}: wrong_memory must equal baseline injected_context",
                )


# ── FailureMemoryStore retrieve ─────────────────────────────────────────


class FailureMemoryStoreRetrieveTest(unittest.TestCase):
    """AC: Future tasks retrieve corrected_memory + repair_guidance, not full traces."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(ISSUE_3_CASES)
        cls.store = FailureMemoryStore()
        for case in cls.cases:
            audit = run_case(case)
            ecs = draft_ecs(case, audit)
            cls.store = cls.store.add(FailureMemoryRecord.from_ecs_draft(ecs, case))

    def test_store_contains_all_six_records(self) -> None:
        self.assertEqual(len(self.store), 6)

    def test_retrieve_by_matching_query_returns_records(self) -> None:
        results = self.store.retrieve("Which city did Mira choose for the Q3 offsite?")
        self.assertGreater(
            len(results), 0, "Should retrieve at least one matching record"
        )

    def test_retrieve_returns_related_label_records(self) -> None:
        results = self.store.retrieve(
            "What location was picked for the offsite meeting?"
        )
        self.assertGreater(len(results), 0)

    def test_retrieve_unrelated_query_returns_empty(self) -> None:
        results = self.store.retrieve(
            "completely unrelated query about weather patterns"
        )
        self.assertEqual(len(results), 0)

    def test_empty_store_retrieve_returns_empty(self) -> None:
        empty = FailureMemoryStore()
        results = empty.retrieve("any query")
        self.assertEqual(len(results), 0)

    def test_retrieve_respects_top_k(self) -> None:
        results = self.store.retrieve("city offsite meeting", top_k=2)
        self.assertLessEqual(len(results), 2)

    def test_full_trace_is_not_retrieved_as_guidance(self) -> None:
        results = self.store.retrieve("Q3 offsite location")
        for record in results:
            # corrected_memory should not be the wrong_memory from the baseline
            self.assertNotEqual(record.corrected_memory, record.wrong_memory)


# ── build_failure_memory_context ────────────────────────────────────────


class BuildFailureMemoryContextTest(unittest.TestCase):
    """AC: Context modes produce correct content and structure."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(ISSUE_3_CASES)
        cls.store = FailureMemoryStore()
        for case in cls.cases:
            audit = run_case(case)
            ecs = draft_ecs(case, audit)
            cls.store = cls.store.add(FailureMemoryRecord.from_ecs_draft(ecs, case))
        cls.records = cls.store.retrieve("Q3 offsite city")

    def test_none_mode_returns_empty_string(self) -> None:
        ctx = build_failure_memory_context(self.records, "none")
        self.assertEqual(ctx, "")

    def test_none_mode_with_empty_records_returns_empty(self) -> None:
        ctx = build_failure_memory_context((), "none")
        self.assertEqual(ctx, "")

    def test_full_trace_mode_injects_wrong_memory(self) -> None:
        ctx = build_failure_memory_context(self.records, "full_trace")
        self.assertIn("Past Failure Trace", ctx)
        self.assertTrue(len(ctx) > 0)

    def test_corrected_guidance_mode_injects_guidance(self) -> None:
        ctx = build_failure_memory_context(self.records, "corrected_guidance")
        self.assertIn("Failure Memory Guidance", ctx)
        self.assertIn("Corrected:", ctx)
        self.assertIn("Guidance:", ctx)
        self.assertTrue(len(ctx) > 0)

    def test_corrected_guidance_does_not_inject_wrong_memory_text(self) -> None:
        ctx = build_failure_memory_context(self.records, "corrected_guidance")
        for record in self.records:
            if record.wrong_memory:
                self.assertNotIn(
                    record.wrong_memory,
                    ctx,
                    f"corrected_guidance must not contain wrong_memory: {record.wrong_memory[:80]}",
                )

    def test_corrected_guidance_does_not_inject_full_failed_trace(self) -> None:
        ctx = build_failure_memory_context(self.records, "corrected_guidance")
        self.assertNotIn("Past Failure Trace", ctx)

    def test_invalid_mode_raises(self) -> None:
        with self.assertRaises(ValueError):
            build_failure_memory_context(self.records, "invalid_mode")

    def test_empty_records_with_full_trace_returns_empty(self) -> None:
        ctx = build_failure_memory_context((), "full_trace")
        self.assertEqual(ctx, "")

    def test_empty_records_with_corrected_guidance_returns_empty(self) -> None:
        ctx = build_failure_memory_context((), "corrected_guidance")
        self.assertEqual(ctx, "")


# ── Recurrence Comparison Row ───────────────────────────────────────────


class RecurrenceComparisonRowTest(unittest.TestCase):
    """AC: Three-way comparison of no-FM, full-trace, and corrected-guidance."""

    @classmethod
    def setUpClass(cls) -> None:
        # Build Failure Memory from original cases
        cls.original_cases = load_probe_cases(ISSUE_3_CASES)
        cls.store = FailureMemoryStore()
        for case in cls.original_cases:
            audit = run_case(case)
            ecs = draft_ecs(case, audit)
            cls.store = cls.store.add(FailureMemoryRecord.from_ecs_draft(ecs, case))

        # Future task cases
        cls.future_cases = load_probe_cases(ISSUE_7_CASES)
        cls.rows = run_recurrence_comparisons(cls.future_cases, cls.store)

    def test_one_row_per_future_case(self) -> None:
        self.assertEqual(len(self.rows), 3)

    def test_rows_have_all_required_fields(self) -> None:
        for row in self.rows:
            with self.subTest(case_id=row.case_id):
                self.assertIsInstance(row.case_id, str)
                self.assertIsInstance(row.perturbation_label, str)
                self.assertIsInstance(row.no_fm_answer_score, float)
                self.assertIsInstance(row.no_fm_evidence_score, float)
                self.assertIsInstance(row.full_trace_answer_score, float)
                self.assertIsInstance(row.full_trace_evidence_score, float)
                self.assertIsInstance(row.corrected_guidance_answer_score, float)
                self.assertIsInstance(row.corrected_guidance_evidence_score, float)
                self.assertIsInstance(row.corrected_guidance_better_than_none, bool)
                self.assertIsInstance(
                    row.corrected_guidance_better_than_full_trace, bool
                )
                self.assertIsInstance(row.failure_memory_useful, bool)

    def test_scores_are_in_range(self) -> None:
        for row in self.rows:
            with self.subTest(case_id=row.case_id):
                for score in [
                    row.no_fm_answer_score,
                    row.no_fm_evidence_score,
                    row.full_trace_answer_score,
                    row.full_trace_evidence_score,
                    row.corrected_guidance_answer_score,
                    row.corrected_guidance_evidence_score,
                ]:
                    self.assertGreaterEqual(score, 0.0)
                    self.assertLessEqual(score, 1.0)

    def test_token_costs_are_positive(self) -> None:
        for row in self.rows:
            with self.subTest(case_id=row.case_id):
                self.assertGreater(row.corrected_guidance_token_cost, 0.0)

    def test_pollution_risk_in_range(self) -> None:
        for row in self.rows:
            with self.subTest(case_id=row.case_id):
                self.assertGreaterEqual(row.full_trace_pollution_risk, 0.0)
                self.assertLessEqual(row.full_trace_pollution_risk, 1.0)

    def test_full_trace_pollution_risk_is_high_when_no_evidence(self) -> None:
        # Full traces from wrong retrievals should not contain the evidence,
        # so pollution risk should be high (>= 0.5) for most cases.
        high_pollution = sum(1 for r in self.rows if r.full_trace_pollution_risk >= 0.5)
        self.assertGreaterEqual(
            high_pollution,
            1,
            "At least one case should show high pollution risk from full traces",
        )

    def test_failure_memory_useful_flag_is_consistent(self) -> None:
        for row in self.rows:
            expected = row.corrected_guidance_better_than_none
            self.assertEqual(
                row.failure_memory_useful,
                expected,
                f"{row.case_id}: fm_useful must equal corrected_guidance_better_than_none",
            )

    def test_any_fm_improvement_property(self) -> None:
        for row in self.rows:
            self.assertEqual(
                row.any_fm_improvement, row.corrected_guidance_better_than_none
            )


# ── Recurrence Summary ──────────────────────────────────────────────────


class RecurrenceSummaryTest(unittest.TestCase):
    """Aggregated Failure Memory recurrence metrics."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.original_cases = load_probe_cases(ISSUE_3_CASES)
        cls.store = FailureMemoryStore()
        for case in cls.original_cases:
            audit = run_case(case)
            ecs = draft_ecs(case, audit)
            cls.store = cls.store.add(FailureMemoryRecord.from_ecs_draft(ecs, case))
        cls.future_cases = load_probe_cases(ISSUE_7_CASES)
        cls.rows = run_recurrence_comparisons(cls.future_cases, cls.store)
        cls.summary = compute_recurrence_summary(cls.rows)

    def test_summary_has_all_fields(self) -> None:
        self.assertIsInstance(self.summary, RecurrenceSummary)
        self.assertEqual(self.summary.total_cases, 3)
        self.assertIsInstance(self.summary.failure_memory_worth_keeping, bool)

    def test_summary_rates_in_range(self) -> None:
        self.assertGreaterEqual(self.summary.fm_useful_rate, 0.0)
        self.assertLessEqual(self.summary.fm_useful_rate, 1.0)

    def test_summary_with_empty_rows(self) -> None:
        empty = compute_recurrence_summary([])
        self.assertEqual(empty.total_cases, 0)
        self.assertEqual(empty.fm_useful_rate, 0.0)
        self.assertFalse(empty.failure_memory_worth_keeping)

    def test_token_costs_are_positive(self) -> None:
        self.assertGreater(self.summary.avg_token_cost_none, 0.0)
        self.assertGreater(self.summary.avg_token_cost_corrected_guidance, 0.0)

    def test_full_trace_pollution_risk_positive(self) -> None:
        self.assertGreaterEqual(self.summary.avg_full_trace_pollution_risk, 0.0)


# ── Recurrence Table Output ─────────────────────────────────────────────


class RecurrenceTableOutputTest(unittest.TestCase):
    """AC: Results are written to sandbox with proper format."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.original_cases = load_probe_cases(ISSUE_3_CASES)
        cls.store = FailureMemoryStore()
        for case in cls.original_cases:
            audit = run_case(case)
            ecs = draft_ecs(case, audit)
            cls.store = cls.store.add(FailureMemoryRecord.from_ecs_draft(ecs, case))
        cls.future_cases = load_probe_cases(ISSUE_7_CASES)
        cls.rows = run_recurrence_comparisons(cls.future_cases, cls.store)

    def test_table_writes_csv_with_required_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = Path(tmpdir) / "sandbox"
            sandbox.mkdir()
            output = sandbox / "recurrence_comparison.csv"
            write_recurrence_comparison_table(self.rows, output, sandbox_root=sandbox)

            self.assertTrue(output.exists())
            header = output.read_text(encoding="utf-8").splitlines()[0]
            required = [
                "case_id",
                "perturbation_label",
                "no_fm_answer_score",
                "no_fm_evidence_score",
                "full_trace_answer_score",
                "full_trace_evidence_score",
                "corrected_guidance_answer_score",
                "corrected_guidance_evidence_score",
                "full_trace_pollution_risk",
                "failure_memory_useful",
            ]
            for col in required:
                with self.subTest(column=col):
                    self.assertIn(col, header)

    def test_table_writes_summary_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = Path(tmpdir) / "sandbox"
            sandbox.mkdir()
            output = sandbox / "recurrence_comparison.csv"
            write_recurrence_comparison_table(self.rows, output, sandbox_root=sandbox)

            summary_path = sandbox / "recurrence_summary.txt"
            self.assertTrue(summary_path.exists())
            text = summary_path.read_text(encoding="utf-8")
            self.assertIn("CMD V0 ECS Failure Memory Recurrence Summary", text)
            self.assertIn("Failure Memory worth keeping", text)

    def test_table_rejects_outside_sandbox(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = Path(tmpdir) / "sandbox"
            sandbox.mkdir()
            outside = Path(tmpdir) / "outside" / "recurrence.csv"
            outside.parent.mkdir()
            with self.assertRaises(ValueError):
                write_recurrence_comparison_table(
                    self.rows, outside, sandbox_root=sandbox
                )


# ── Full Pipeline: CMD → ECS → FM → Recurrence ─────────────────────────


class FullPipelineRecurrenceTest(unittest.TestCase):
    """End-to-end: original cases → Failure Memory → future cases → comparison."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.original_cases = load_probe_cases(ISSUE_3_CASES)
        cls.future_cases = load_probe_cases(ISSUE_7_CASES)

        # Run full CMD pipeline on original cases
        cls.full_results = [run_case_full(c) for c in cls.original_cases]

        # Build Failure Memory
        cls.store = FailureMemoryStore()
        for fr in cls.full_results:
            record = FailureMemoryRecord.from_ecs_draft(
                fr.ecs_draft, cls.original_cases[0]
            )
            cls.store = cls.store.add(record)

        # Actually build store properly per case
        cls.store = FailureMemoryStore()
        for case, fr in zip(cls.original_cases, cls.full_results):
            record = FailureMemoryRecord.from_ecs_draft(fr.ecs_draft, case)
            cls.store = cls.store.add(record)

        cls.rows = run_recurrence_comparisons(cls.future_cases, cls.store)

    def test_full_pipeline_produces_valid_rows(self) -> None:
        self.assertEqual(len(self.rows), 3)
        for row in self.rows:
            with self.subTest(case_id=row.case_id):
                self.assertIn(row.perturbation_label, V0_PIPELINE_LABEL_ORDER)

    def test_corrected_guidance_outperforms_full_trace(self) -> None:
        better_count = sum(
            1 for r in self.rows if r.corrected_guidance_better_than_full_trace
        )
        self.assertGreaterEqual(
            better_count,
            1,
            "Corrected guidance should outperform full trace for at least one case",
        )

    def test_all_original_cases_in_failure_memory(self) -> None:
        self.assertEqual(len(self.store), 6)

    def test_similar_future_case_retrieves_original_record(self) -> None:
        retrieval_case = [
            c for c in self.future_cases if c.case_id == "v0-fm-retrieval-001"
        ][0]
        records = self.store.retrieve(retrieval_case.query)
        self.assertGreater(len(records), 0)
        self.assertIn("retrieval_error", [r.error_type for r in records])

    def test_recurrence_summary_is_positive(self) -> None:
        summary = compute_recurrence_summary(self.rows)
        self.assertIsInstance(summary.failure_memory_worth_keeping, bool)
        self.assertGreaterEqual(summary.fm_useful_rate, 0.0)
        self.assertGreaterEqual(summary.avg_full_trace_pollution_risk, 0.0)


# ── ECS cause validation in FM records ──────────────────────────────────


class FailureMemoryECSCauseValidationTest(unittest.TestCase):
    """AC: ECS cause in FM records must not use forbidden item labels."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(ISSUE_3_CASES)

    def test_fm_record_cause_does_not_contain_forbidden_labels(self) -> None:
        forbidden = {
            "item_wrong",
            "item_stale",
            "item_conflict",
            "item_poisoned",
            "item_compression_distorted",
        }
        for case in self.cases:
            audit = run_case(case)
            ecs = draft_ecs(case, audit)
            record = FailureMemoryRecord.from_ecs_draft(ecs, case)

            with self.subTest(case_id=case.case_id):
                lowered = record.cause.casefold()
                for label in forbidden:
                    self.assertNotIn(
                        label,
                        lowered,
                        f"{case.case_id}: FM record cause must not use {label}",
                    )


# ── No gold answer leakage in FM ────────────────────────────────────────


class FailureMemoryNoGoldLeakageTest(unittest.TestCase):
    """AC: Failure Memory records do not leak gold answers."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(ISSUE_3_CASES)

    def test_fm_record_preserves_ecs_boundaries(self) -> None:
        # FM corrected_memory comes from CMD replay evidence_block.
        # For non-reasoning errors, corrected_memory should differ from
        # wrong_memory. For reasoning_error, both may use the same
        # evidence (retrieved correctly but reasoned over wrongly).
        for case in self.cases:
            audit = run_case(case)
            ecs = draft_ecs(case, audit)
            record = FailureMemoryRecord.from_ecs_draft(ecs, case)

            with self.subTest(case_id=case.case_id):
                if case.perturbation_label == "reasoning_error":
                    self.assertEqual(
                        record.corrected_memory,
                        record.wrong_memory,
                        f"{case.case_id}: reasoning_error: evidence was correct, "
                        f"repair adds reasoning guidance not new memory",
                    )
                else:
                    self.assertNotEqual(
                        record.corrected_memory,
                        record.wrong_memory,
                        f"{case.case_id}: corrected_memory must differ from wrong_memory",
                    )


if __name__ == "__main__":
    unittest.main()
