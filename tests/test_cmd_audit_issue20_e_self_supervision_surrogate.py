"""Self-Supervision Surrogate Gap tests — Issue 0020-E / 0021 Step 2."""

from pathlib import Path
import unittest

from cmd_audit import (
    SurrogateGapRow,
    SurrogateGapSummary,
    compute_surrogate_gap_summary,
    load_probe_cases,
    measure_surrogate_gap,
    measure_surrogate_gaps,
)
from cmd_audit.eval import GOLD_DEPENDENT_LABELS


WRITE_FIXTURE = Path("data/probe_cases/v0_issue3_cases.json")
RETRIEVAL_FIXTURE = Path("data/probe_cases/v0_retrieval_error_case.json")


class GoldDependentLabelsTest(unittest.TestCase):
    """AC: GOLD_DEPENDENT_LABELS has 4 labels."""

    def test_four_labels(self) -> None:
        self.assertEqual(len(GOLD_DEPENDENT_LABELS), 4)

    def test_labels_are_pipeline_labels(self) -> None:
        for label in GOLD_DEPENDENT_LABELS:
            self.assertIn(label, (
                "write_error",
                "compression_error",
                "premature_extraction_error",
                "injection_error",
            ))


class MeasureSurrogateGapTest(unittest.TestCase):
    """AC: measure_surrogate_gap produces gap data for gold-dependent cases."""

    def test_gold_dependent_case_returns_row(self) -> None:
        cases = load_probe_cases(WRITE_FIXTURE)
        for case in cases:
            if case.perturbation_label in GOLD_DEPENDENT_LABELS:
                with self.subTest(case_id=case.case_id, label=case.perturbation_label):
                    row = measure_surrogate_gap(case)
                    self.assertIsNotNone(row)
                    self.assertIsInstance(row, SurrogateGapRow)
                    self.assertEqual(row.case_id, case.case_id)
                    self.assertEqual(row.label, case.perturbation_label)
                break  # Test one case

    def test_non_gold_dependent_returns_none(self) -> None:
        case = load_probe_cases(RETRIEVAL_FIXTURE)[0]
        self.assertNotIn(case.perturbation_label, GOLD_DEPENDENT_LABELS)
        row = measure_surrogate_gap(case)
        self.assertIsNone(row)

    def test_gap_is_computed(self) -> None:
        cases = load_probe_cases(WRITE_FIXTURE)
        for case in cases:
            if case.perturbation_label in GOLD_DEPENDENT_LABELS:
                row = measure_surrogate_gap(case)
                self.assertIsNotNone(row)
                expected_gap = row.gold_recovery_gain - row.surrogate_recovery_gain
                self.assertAlmostEqual(row.gap, expected_gap)
                break

    def test_surrogate_found_flag(self) -> None:
        cases = load_probe_cases(WRITE_FIXTURE)
        for case in cases:
            if case.perturbation_label in GOLD_DEPENDENT_LABELS:
                row = measure_surrogate_gap(case)
                self.assertIsInstance(row.surrogate_found, bool)
                break

    def test_llm_stack_agent_path_scores_answers(self) -> None:
        cases = load_probe_cases(WRITE_FIXTURE)
        case = next(c for c in cases if c.perturbation_label in GOLD_DEPENDENT_LABELS)
        calls: list[str] = []

        def agent_generate(query: str, context: str) -> str:
            calls.append(context)
            if "SURROGATE EVIDENCE BLOCK" in context and "Madrid" in context:
                return "Madrid"
            return "baseline failed answer"

        def scorer(gold_evidence, text: str) -> float:
            return 1.0 if "Madrid" in text else 0.0

        row = measure_surrogate_gap(
            case,
            agent_generate=agent_generate,
            scorer=scorer,
        )

        self.assertIsNotNone(row)
        self.assertGreaterEqual(row.gold_recovery_gain, 0.0)
        self.assertTrue(any("GOLD EVIDENCE BLOCK" in c for c in calls))
        self.assertTrue(any("SURROGATE EVIDENCE BLOCK" in c for c in calls))


class MeasureSurrogateGapsBatchTest(unittest.TestCase):
    """AC: measure_surrogate_gaps processes multiple cases."""

    def test_batch_processing(self) -> None:
        cases = load_probe_cases(WRITE_FIXTURE)
        rows = measure_surrogate_gaps(cases)
        self.assertIsInstance(rows, tuple)
        # Should only include gold-dependent cases
        for row in rows:
            self.assertIn(row.label, GOLD_DEPENDENT_LABELS)

    def test_empty_cases(self) -> None:
        rows = measure_surrogate_gaps([])
        self.assertEqual(rows, ())


class ComputeSurrogateGapSummaryTest(unittest.TestCase):
    """AC: Summary aggregates gap statistics."""

    def test_summary_from_rows(self) -> None:
        cases = load_probe_cases(WRITE_FIXTURE)
        rows = measure_surrogate_gaps(cases)
        if rows:
            summary = compute_surrogate_gap_summary(rows)
            self.assertIsInstance(summary, SurrogateGapSummary)
            self.assertEqual(summary.total_cases, len(rows))
            self.assertGreaterEqual(summary.avg_gap, summary.min_gap)
            self.assertLessEqual(summary.avg_gap, summary.max_gap)
            self.assertGreaterEqual(summary.pct_surrogate_found, 0.0)
            self.assertLessEqual(summary.pct_surrogate_found, 1.0)

    def test_empty_rows(self) -> None:
        summary = compute_surrogate_gap_summary(())
        self.assertEqual(summary.total_cases, 0)
        self.assertEqual(summary.avg_gap, 0.0)


class SurrogateGapRowImmutabilityTest(unittest.TestCase):
    """AC: SurrogateGapRow is frozen."""

    def test_frozen(self) -> None:
        row = SurrogateGapRow(
            case_id="test",
            label="write_error",
            gold_recovery_gain=0.8,
            surrogate_recovery_gain=0.3,
            gap=0.5,
            surrogate_found=True,
        )
        with self.assertRaises(AttributeError):
            row.gap = 0.6


if __name__ == "__main__":
    unittest.main()
