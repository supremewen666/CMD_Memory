"""Tests for version gate enforcement — Issue 0010."""

from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from cmd_audit.version_gates import (
    GateCriterion,
    GateResult,
    GateReview,
    _check_accuracy_top2,
    _check_confusion_diagonal,
    _check_macro_f1,
    _check_repair_distribution,
    _read_comparison_csv,
    _read_confusion_csv,
    _read_repair_csv,
    check_v0_to_v1_gate,
    check_v1_to_v2_gate,
    write_gate_review,
    write_gate_status,
)


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    """Write a list of dict rows as a CSV file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# ── Data type tests ────────────────────────────────────────────────────


class GateCriterionCreationTest(unittest.TestCase):
    def test_passed_criterion(self):
        c = GateCriterion(
            criterion_id="test_crit",
            description="A test criterion",
            artifact_path="artifacts/test.csv",
            threshold="value > 0.5",
            passed=True,
            evidence="value=0.8",
            missing="",
        )
        self.assertTrue(c.passed)
        self.assertEqual(c.criterion_id, "test_crit")
        self.assertEqual(c.missing, "")
        self.assertEqual(c.evidence, "value=0.8")

    def test_failed_criterion_with_missing(self):
        c = GateCriterion(
            criterion_id="test_fail",
            description="A failing criterion",
            artifact_path="artifacts/test.csv",
            threshold="value > 0.9",
            passed=False,
            evidence="value=0.3",
            missing="Need value > 0.9, got 0.3",
        )
        self.assertFalse(c.passed)
        self.assertIn("Need value", c.missing)

    def test_criterion_immutable(self):
        c = GateCriterion(
            criterion_id="immut",
            description="desc",
            artifact_path="p",
            threshold="t",
            passed=True,
            evidence="e",
            missing="",
        )
        with self.assertRaises(Exception):
            c.passed = False


class GateResultCreationTest(unittest.TestCase):
    def test_result_all_passed_true(self):
        c1 = GateCriterion("a", "d", "p", "t", True, "e", "")
        c2 = GateCriterion("b", "d", "p", "t", True, "e", "")
        result = GateResult("V0→V1", (c1, c2), True, "2026-05-10T00:00:00Z")
        self.assertTrue(result.all_passed)
        self.assertEqual(len(result.criteria), 2)

    def test_result_all_passed_false(self):
        c1 = GateCriterion("a", "d", "p", "t", True, "e", "")
        c2 = GateCriterion("b", "d", "p", "t", False, "e", "m")
        result = GateResult("V0→V1", (c1, c2), False, "2026-05-10T00:00:00Z")
        self.assertFalse(result.all_passed)

    def test_result_immutable(self):
        c = GateCriterion("a", "d", "p", "t", True, "e", "")
        result = GateResult("V0→V1", (c,), True, "2026-05-10T00:00:00Z")
        with self.assertRaises(Exception):
            result.all_passed = False


class GateReviewCreationTest(unittest.TestCase):
    def test_valid_review(self):
        review = GateReview(
            gate_id="V0→V1",
            reviewer="HITL",
            decision="approved",
            rationale="All criteria met.",
            missing_evidence="",
            reviewed_at="2026-05-10T12:00:00Z",
        )
        self.assertEqual(review.decision, "approved")
        self.assertEqual(review.gate_id, "V0→V1")

    def test_review_rejects_invalid_decision(self):
        with self.assertRaises(ValueError):
            GateReview(
                gate_id="V0→V1",
                reviewer="HITL",
                decision="maybe_later",
                rationale="...",
                missing_evidence="",
                reviewed_at="2026-05-10T12:00:00Z",
            )

    def test_deferred_review_with_missing(self):
        review = GateReview(
            gate_id="V0→V1",
            reviewer="HITL",
            decision="deferred",
            rationale="Need more probe cases.",
            missing_evidence="Only 6 smoke cases; need 50+ for paper claim.",
            reviewed_at="2026-05-10T12:00:00Z",
        )
        self.assertEqual(review.decision, "deferred")
        self.assertIn("50+", review.missing_evidence)


# ── CSV reader tests ────────────────────────────────────────────────────


class ComparisonCSVReaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "comparison_metrics.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_all_systems(self):
        _write_csv(
            self.path,
            [
                {
                    "system_name": "CMD-Audit",
                    "attribution_accuracy": "1.0",
                    "macro_f1": "1.0",
                    "top2_accuracy": "1.0",
                    "cost_per_diagnosis": "6.2",
                },
                {
                    "system_name": "evidence_recall",
                    "attribution_accuracy": "0.833",
                    "macro_f1": "0.778",
                    "top2_accuracy": "0.833",
                    "cost_per_diagnosis": "0.05",
                },
            ],
        )
        data = _read_comparison_csv(self.path)
        self.assertIn("CMD-Audit", data)
        self.assertAlmostEqual(data["CMD-Audit"]["macro_f1"], 1.0)

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            _read_comparison_csv(Path(self.tmp.name) / "nonexistent.csv")


class ConfusionCSVReaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "confusion.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_matrix(self):
        _write_csv(
            self.path,
            [
                {
                    "gold_label": "write_error",
                    "write_error": "1",
                    "compression_error": "0",
                    "retrieval_error": "0",
                },
                {
                    "gold_label": "retrieval_error",
                    "write_error": "0",
                    "compression_error": "0",
                    "retrieval_error": "1",
                },
            ],
        )
        data = _read_confusion_csv(self.path)
        self.assertEqual(data["write_error"]["write_error"], 1)
        self.assertEqual(data["write_error"]["retrieval_error"], 0)

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            _read_confusion_csv(Path(self.tmp.name) / "nonexistent.csv")


class RepairCSVReaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "post_repair.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_assessments(self):
        _write_csv(
            self.path,
            [
                {
                    "case_id": "v0-write-001",
                    "repair_assessment": "recovered",
                    "other": "x",
                },
                {
                    "case_id": "v0-compression-001",
                    "repair_assessment": "recovered",
                    "other": "y",
                },
                {
                    "case_id": "v0-fail-001",
                    "repair_assessment": "failed",
                    "other": "z",
                },
            ],
        )
        assessments = _read_repair_csv(self.path)
        self.assertEqual(assessments, ["recovered", "recovered", "failed"])


# ── Criterion-level check tests ─────────────────────────────────────────


class MacroF1CheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "comparison_metrics.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def test_passes_when_cmd_beats_all_baselines(self):
        _write_csv(
            self.path,
            [
                {"system_name": "CMD-Audit", "macro_f1": "0.92"},
                {"system_name": "evidence_recall", "macro_f1": "0.78"},
                {"system_name": "subagent_judge", "macro_f1": "0.80"},
                {"system_name": "random_label", "macro_f1": "0.17"},
            ],
        )
        c = _check_macro_f1(self.path)
        self.assertTrue(c.passed)
        self.assertEqual(c.criterion_id, "macro_f1_exceeds_baselines")

    def test_fails_when_baseline_beats_cmd(self):
        _write_csv(
            self.path,
            [
                {"system_name": "CMD-Audit", "macro_f1": "0.70"},
                {"system_name": "evidence_recall", "macro_f1": "0.85"},
                {"system_name": "subagent_judge", "macro_f1": "0.80"},
                {"system_name": "random_label", "macro_f1": "0.17"},
            ],
        )
        c = _check_macro_f1(self.path)
        self.assertFalse(c.passed)
        self.assertIn("0.70", c.missing)

    def test_fails_when_cmd_missing_from_csv(self):
        _write_csv(
            self.path,
            [{"system_name": "evidence_recall", "macro_f1": "0.78"}],
        )
        c = _check_macro_f1(self.path)
        self.assertFalse(c.passed)

    def test_fails_when_artifact_missing(self):
        c = _check_macro_f1(Path(self.tmp.name) / "nope.csv")
        self.assertFalse(c.passed)
        self.assertIn("not found", c.missing)


class ConfusionDiagonalCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "confusion.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def test_passes_with_perfect_diagonal(self):
        labels = [
            "write_error",
            "compression_error",
            "premature_extraction_error",
            "retrieval_error",
            "injection_error",
            "reasoning_error",
        ]
        rows = []
        for gold in labels:
            row = {"gold_label": gold}
            for pred in labels:
                row[pred] = "1" if pred == gold else "0"
            rows.append(row)
        _write_csv(self.path, rows)

        c = _check_confusion_diagonal(self.path)
        self.assertTrue(c.passed, f"Expected pass but got missing={c.missing}")

    def test_fails_with_off_diagonal(self):
        labels = [
            "write_error",
            "compression_error",
            "premature_extraction_error",
            "retrieval_error",
            "injection_error",
            "reasoning_error",
        ]
        rows = []
        for gold in labels:
            row = {"gold_label": gold}
            for pred in labels:
                # put 2 off-diagonal entries for write_error
                if gold == "write_error" and pred == "compression_error":
                    row[pred] = "2"
                elif pred == gold:
                    row[pred] = "1"
                else:
                    row[pred] = "0"
            rows.append(row)
        _write_csv(self.path, rows)

        c = _check_confusion_diagonal(self.path)
        self.assertFalse(c.passed)
        self.assertIn("write_error", c.missing)


class AccuracyTop2CheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "comparison_metrics.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def test_passes_when_cmd_beats_all(self):
        _write_csv(
            self.path,
            [
                {
                    "system_name": "CMD-Audit",
                    "attribution_accuracy": "0.95",
                    "top2_accuracy": "1.0",
                },
                {
                    "system_name": "evidence_recall",
                    "attribution_accuracy": "0.83",
                    "top2_accuracy": "0.83",
                },
                {
                    "system_name": "subagent_judge",
                    "attribution_accuracy": "0.80",
                    "top2_accuracy": "0.80",
                },
                {
                    "system_name": "random_label",
                    "attribution_accuracy": "0.17",
                    "top2_accuracy": "0.67",
                },
            ],
        )
        c = _check_accuracy_top2(self.path)
        self.assertTrue(c.passed)

    def test_fails_when_accuracy_lower(self):
        _write_csv(
            self.path,
            [
                {
                    "system_name": "CMD-Audit",
                    "attribution_accuracy": "0.70",
                    "top2_accuracy": "1.0",
                },
                {
                    "system_name": "evidence_recall",
                    "attribution_accuracy": "0.83",
                    "top2_accuracy": "0.83",
                },
                {
                    "system_name": "subagent_judge",
                    "attribution_accuracy": "0.80",
                    "top2_accuracy": "0.80",
                },
                {
                    "system_name": "random_label",
                    "attribution_accuracy": "0.17",
                    "top2_accuracy": "0.67",
                },
            ],
        )
        c = _check_accuracy_top2(self.path)
        self.assertFalse(c.passed)

    def test_fails_when_top2_lower(self):
        _write_csv(
            self.path,
            [
                {
                    "system_name": "CMD-Audit",
                    "attribution_accuracy": "0.95",
                    "top2_accuracy": "0.65",
                },
                {
                    "system_name": "evidence_recall",
                    "attribution_accuracy": "0.83",
                    "top2_accuracy": "0.83",
                },
                {
                    "system_name": "subagent_judge",
                    "attribution_accuracy": "0.80",
                    "top2_accuracy": "0.80",
                },
                {
                    "system_name": "random_label",
                    "attribution_accuracy": "0.17",
                    "top2_accuracy": "0.67",
                },
            ],
        )
        c = _check_accuracy_top2(self.path)
        self.assertFalse(c.passed)


class RepairDistributionCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "post_repair.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def test_passes_with_high_recovery(self):
        _write_csv(
            self.path,
            [
                {"case_id": "a", "repair_assessment": "recovered"},
                {"case_id": "b", "repair_assessment": "recovered"},
                {"case_id": "c", "repair_assessment": "recovered"},
                {"case_id": "d", "repair_assessment": "partial"},
                {"case_id": "e", "repair_assessment": "failed"},
            ],
        )
        c = _check_repair_distribution(self.path)
        self.assertTrue(c.passed)

    def test_fails_when_below_recovery_threshold(self):
        """rec+partial > failed but recovered_rate=0.25 < 0.5 — must fail."""
        _write_csv(
            self.path,
            [
                {"case_id": "a", "repair_assessment": "recovered"},
                {"case_id": "b", "repair_assessment": "partial"},
                {"case_id": "c", "repair_assessment": "partial"},
                {"case_id": "d", "repair_assessment": "failed"},
            ],
        )
        c = _check_repair_distribution(self.path)
        self.assertFalse(c.passed)

    def test_fails_when_recovery_rate_low(self):
        _write_csv(
            self.path,
            [
                {"case_id": "a", "repair_assessment": "recovered"},
                {"case_id": "b", "repair_assessment": "failed"},
                {"case_id": "c", "repair_assessment": "failed"},
                {"case_id": "d", "repair_assessment": "failed"},
                {"case_id": "e", "repair_assessment": "failed"},
            ],
        )
        c = _check_repair_distribution(self.path)
        self.assertFalse(c.passed)

    def test_fails_when_failed_dominates(self):
        _write_csv(
            self.path,
            [
                {"case_id": "a", "repair_assessment": "recovered"},
                {"case_id": "b", "repair_assessment": "failed"},
                {"case_id": "c", "repair_assessment": "failed"},
            ],
        )
        c = _check_repair_distribution(self.path)
        self.assertFalse(c.passed)  # rec+partial(1) <= failed(2)

    def test_fails_with_empty_table(self):
        # Write CSV with headers only (no data rows)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("case_id,repair_assessment\n", encoding="utf-8")
        c = _check_repair_distribution(self.path)
        self.assertFalse(c.passed)


# ── Full V0→V1 gate check tests ────────────────────────────────────────


class V0V1GateCheckWithRealArtifactsTest(unittest.TestCase):
    def test_all_criteria_pass_with_current_artifacts(self):
        """Verify the four V0 gate criteria pass against the current smoke artifacts."""
        result = check_v0_to_v1_gate()
        self.assertEqual(result.gate_id, "V0→V1")
        self.assertEqual(len(result.criteria), 4)
        self.assertTrue(result.all_passed)
        self.assertIsNotNone(result.checked_at)
        for c in result.criteria:
            self.assertTrue(c.passed, f"{c.criterion_id} should pass: {c.missing}")

    def test_criterion_ids_match_spec(self):
        result = check_v0_to_v1_gate()
        ids = tuple(c.criterion_id for c in result.criteria)
        expected = (
            "macro_f1_exceeds_baselines",
            "confusion_diagonal_dominance",
            "accuracy_top2_exceeds_baselines",
            "repair_assessment_distribution",
        )
        self.assertEqual(ids, expected)

    def test_result_is_immutable(self):
        result = check_v0_to_v1_gate()
        with self.assertRaises(Exception):
            result.all_passed = False

    def test_each_criterion_has_evidence(self):
        result = check_v0_to_v1_gate()
        for c in result.criteria:
            self.assertTrue(len(c.evidence) > 0, f"{c.criterion_id} has empty evidence")


class V0V1GateCheckWithTempArtifactsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.artifacts = Path(self.tmp.name) / "artifacts"
        self.sandbox = self.artifacts / "sandbox"
        self.artifacts.mkdir(parents=True)
        self.sandbox.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_passing_artifacts(self):
        """Write artifacts that pass all four criteria."""
        # comparison_metrics.csv: CMD beats all baselines
        _write_csv(
            self.artifacts / "comparison_metrics.csv",
            [
                {
                    "system_name": "CMD-Audit",
                    "attribution_accuracy": "0.95",
                    "macro_f1": "0.92",
                    "top2_accuracy": "0.98",
                    "cost_per_diagnosis": "6.2",
                },
                {
                    "system_name": "evidence_recall",
                    "attribution_accuracy": "0.83",
                    "macro_f1": "0.78",
                    "top2_accuracy": "0.83",
                    "cost_per_diagnosis": "0.05",
                },
                {
                    "system_name": "subagent_judge",
                    "attribution_accuracy": "0.80",
                    "macro_f1": "0.78",
                    "top2_accuracy": "0.83",
                    "cost_per_diagnosis": "1.0",
                },
                {
                    "system_name": "random_label",
                    "attribution_accuracy": "0.17",
                    "macro_f1": "0.17",
                    "top2_accuracy": "0.67",
                    "cost_per_diagnosis": "0.01",
                },
            ],
        )
        # confusion matrix: perfect diagonal for all V0 labels
        labels = [
            "write_error",
            "compression_error",
            "premature_extraction_error",
            "retrieval_error",
            "injection_error",
            "reasoning_error",
        ]
        confusion_rows = []
        for gold in labels:
            row = {"gold_label": gold}
            for pred in labels:
                row[pred] = "1" if pred == gold else "0"
            confusion_rows.append(row)
        _write_csv(self.artifacts / "attribution_confusion_matrix.csv", confusion_rows)
        # post-repair: all recovered
        _write_csv(
            self.sandbox / "post_repair_table.csv",
            [
                {"case_id": f"v0-{label}-001", "repair_assessment": "recovered"}
                for label in labels
            ],
        )

    def test_all_pass_with_passing_artifacts(self):
        self._write_passing_artifacts()
        result = check_v0_to_v1_gate(
            artifacts_dir=self.artifacts, sandbox_dir=self.sandbox
        )
        self.assertTrue(result.all_passed)

    def test_fails_when_comparison_missing(self):
        self._write_passing_artifacts()
        (self.artifacts / "comparison_metrics.csv").unlink()
        result = check_v0_to_v1_gate(
            artifacts_dir=self.artifacts, sandbox_dir=self.sandbox
        )
        self.assertFalse(result.all_passed)
        # macro_f1 and accuracy_top2 both depend on comparison_metrics.csv
        failed = [c for c in result.criteria if not c.passed]
        self.assertEqual(len(failed), 2)

    def test_fails_when_confusion_missing(self):
        self._write_passing_artifacts()
        (self.artifacts / "attribution_confusion_matrix.csv").unlink()
        result = check_v0_to_v1_gate(
            artifacts_dir=self.artifacts, sandbox_dir=self.sandbox
        )
        self.assertFalse(result.all_passed)
        confusion = [
            c
            for c in result.criteria
            if c.criterion_id == "confusion_diagonal_dominance"
        ]
        self.assertFalse(confusion[0].passed)

    def test_fails_when_repair_missing(self):
        self._write_passing_artifacts()
        (self.sandbox / "post_repair_table.csv").unlink()
        result = check_v0_to_v1_gate(
            artifacts_dir=self.artifacts, sandbox_dir=self.sandbox
        )
        self.assertFalse(result.all_passed)
        repair = [
            c
            for c in result.criteria
            if c.criterion_id == "repair_assessment_distribution"
        ]
        self.assertFalse(repair[0].passed)

    def test_fails_when_macro_f1_insufficient(self):
        self._write_passing_artifacts()
        # Overwrite with CMD below baselines
        _write_csv(
            self.artifacts / "comparison_metrics.csv",
            [
                {
                    "system_name": "CMD-Audit",
                    "attribution_accuracy": "0.95",
                    "macro_f1": "0.50",
                    "top2_accuracy": "0.98",
                    "cost_per_diagnosis": "6.2",
                },
                {
                    "system_name": "evidence_recall",
                    "attribution_accuracy": "0.83",
                    "macro_f1": "0.78",
                    "top2_accuracy": "0.83",
                    "cost_per_diagnosis": "0.05",
                },
                {
                    "system_name": "subagent_judge",
                    "attribution_accuracy": "0.80",
                    "macro_f1": "0.78",
                    "top2_accuracy": "0.83",
                    "cost_per_diagnosis": "1.0",
                },
                {
                    "system_name": "random_label",
                    "attribution_accuracy": "0.17",
                    "macro_f1": "0.17",
                    "top2_accuracy": "0.67",
                    "cost_per_diagnosis": "0.01",
                },
            ],
        )
        result = check_v0_to_v1_gate(
            artifacts_dir=self.artifacts, sandbox_dir=self.sandbox
        )
        self.assertFalse(result.all_passed)
        f1_crit = [
            c for c in result.criteria if c.criterion_id == "macro_f1_exceeds_baselines"
        ]
        self.assertFalse(f1_crit[0].passed)


# ── V1→V2 gate check tests ──────────────────────────────────────────────


class V1V2GateCheckTest(unittest.TestCase):
    def test_returns_not_met_stub(self):
        result = check_v1_to_v2_gate()
        self.assertEqual(result.gate_id, "V1→V2")
        self.assertFalse(result.all_passed)
        self.assertEqual(len(result.criteria), 2)
        c = result.criteria[0]
        self.assertEqual(c.criterion_id, "adapter_integration_count")
        self.assertFalse(c.passed)
        self.assertIn("0 adapter integrations", c.evidence)
        self.assertEqual(result.criteria[1].criterion_id, "provenance_hmac_tamper_free")

    def test_result_has_timestamp(self):
        result = check_v1_to_v2_gate()
        self.assertIsNotNone(result.checked_at)
        self.assertIn("T", result.checked_at)


# ── Gate status and review write tests ──────────────────────────────────


class GateStatusWriteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sandbox = Path(self.tmp.name) / "sandbox"
        self.sandbox.mkdir(parents=True)
        self.output = self.sandbox / "V0V1_gate_status.txt"

    def tearDown(self):
        self.tmp.cleanup()

    def test_writes_status_file(self):
        c = GateCriterion("test", "desc", "p", "t", True, "evidence", "")
        result = GateResult("V0→V1", (c,), True, "2026-05-10T00:00:00Z")
        path = write_gate_status(result, self.output, sandbox_root=self.sandbox)
        self.assertTrue(path.exists())
        content = path.read_text()
        self.assertIn("V0→V1", content)
        self.assertIn("PASS", content)

    def test_output_contains_all_criteria(self):
        c1 = GateCriterion("crit_a", "d", "p", "t", True, "e", "")
        c2 = GateCriterion("crit_b", "d", "p", "t", False, "e2", "m2")
        result = GateResult("V0→V1", (c1, c2), False, "2026-05-10T00:00:00Z")
        path = write_gate_status(result, self.output, sandbox_root=self.sandbox)
        content = path.read_text()
        self.assertIn("crit_a", content)
        self.assertIn("crit_b", content)
        self.assertIn("PASS", content)
        self.assertIn("FAIL", content)
        self.assertIn("m2", content)

    def test_sandbox_path_enforced(self):
        c = GateCriterion("test", "d", "p", "t", True, "e", "")
        result = GateResult("V0→V1", (c,), True, "2026-05-10T00:00:00Z")
        bad_path = Path(self.tmp.name) / "outside_sandbox.txt"
        with self.assertRaises(ValueError):
            write_gate_status(result, bad_path, sandbox_root=self.sandbox)

    def test_creates_parent_directories(self):
        c = GateCriterion("test", "d", "p", "t", True, "e", "")
        result = GateResult("V0→V1", (c,), True, "2026-05-10T00:00:00Z")
        deep = self.sandbox / "deep" / "nested" / "status.txt"
        path = write_gate_status(result, deep, sandbox_root=self.sandbox)
        self.assertTrue(path.exists())


class GateReviewWriteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sandbox = Path(self.tmp.name) / "sandbox"
        self.sandbox.mkdir(parents=True)
        self.output = self.sandbox / "V0V1_gate_review.txt"

    def tearDown(self):
        self.tmp.cleanup()

    def test_writes_review_file(self):
        review = GateReview(
            gate_id="V0→V1",
            reviewer="HITL",
            decision="approved",
            rationale="All criteria met with smoke artifacts.",
            missing_evidence="",
            reviewed_at="2026-05-10T12:00:00Z",
        )
        path = write_gate_review(review, self.output, sandbox_root=self.sandbox)
        self.assertTrue(path.exists())
        content = path.read_text()
        self.assertIn("V0→V1", content)
        self.assertIn("approved", content)
        self.assertIn("HITL", content)

    def test_dated_review_format(self):
        review = GateReview(
            gate_id="V0→V1",
            reviewer="HITL",
            decision="deferred",
            rationale="Need more probe cases.",
            missing_evidence="6 smoke cases is insufficient for paper claim.",
            reviewed_at="2026-05-10T12:00:00Z",
        )
        path = write_gate_review(review, self.output, sandbox_root=self.sandbox)
        content = path.read_text()
        self.assertIn("2026-05-10", content)
        self.assertIn("deferred", content)
        self.assertIn("Need more probe cases", content)
        self.assertIn("insufficient", content)

    def test_sandbox_path_enforced(self):
        review = GateReview(
            gate_id="V0→V1",
            reviewer="HITL",
            decision="approved",
            rationale="ok",
            missing_evidence="",
            reviewed_at="2026-05-10T12:00:00Z",
        )
        bad_path = Path(self.tmp.name) / "outside_review.txt"
        with self.assertRaises(ValueError):
            write_gate_review(review, bad_path, sandbox_root=self.sandbox)


# ── Gate does not block implementation ──────────────────────────────────


class GatesDoNotBlockImplementationTest(unittest.TestCase):
    def test_gate_check_runs_independently(self):
        """Gate check should be callable without affecting other modules."""
        result = check_v0_to_v1_gate()
        self.assertIsInstance(result, GateResult)
        # Gate check does not write to disk by itself
        # Gate check does not import from harness or baselines

    def test_v1_v2_stub_does_not_crash(self):
        result = check_v1_to_v2_gate()
        self.assertIsInstance(result, GateResult)
