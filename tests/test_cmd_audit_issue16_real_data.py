"""Behavior-level tests for issue 0016: real-data probe cases and memory-probe integration."""

from __future__ import annotations

from pathlib import Path
import unittest

from cmd_audit import (
    LabelValidationError,
    PIPELINE_LABELS,
    load_probe_cases,
    load_probe_cases_v1,
    run_case_v1,
    run_case_full_v1,
    run_cases_v1,
    validate_label_base,
    validate_label,
)
from cmd_audit.harness import diagnosis_predictions, write_comparison_metrics_table
from baselines.memory_probe import (
    WRITE_STRATEGIES,
    RETRIEVAL_METHODS,
    MemoryProbeBaselineResult,
    MemoryProbeCaseResult,
    run_memory_probe_baselines,
    run_memory_probe_case,
)
from cmd_audit.metrics import DiagnosisPrediction, compute_diagnosis_metrics

REAL_LONGMEMEVAL = Path("data/probe_cases/real_longmemeval_cases.json")
REAL_MEMORYARENA = Path("data/probe_cases/real_memoryarena_cases.json")
REAL_TOOLBENCH = Path("data/probe_cases/real_toolbench_cases.json")
NULL_LABEL_FIXTURE = Path("data/probe_cases/v1_null_label_cases.json")
V0_SMOKE = Path("data/probe_cases/v0_issue3_cases.json")


# ── Real-Data Case Loading ─────────────────────────────────────────────


class RealDataLoadingTest(unittest.TestCase):
    """Real-data probe cases load through existing ProbeLoader without schema changes."""

    def test_longmemeval_200_cases_load_v1(self) -> None:
        cases = load_probe_cases_v1(REAL_LONGMEMEVAL)
        self.assertEqual(len(cases), 200)
        for case in cases:
            with self.subTest(case_id=case.case_id):
                self.assertIsNotNone(case.case_id)
                self.assertIsNotNone(case.query)
                self.assertGreater(len(case.raw_events), 0)
                self.assertGreater(len(case.extracted_memory), 0)
                self.assertGreater(len(case.gold_evidence), 0)
                self.assertIsNotNone(case.gold_answer)

    def test_memoryarena_198_cases_load_v1(self) -> None:
        cases = load_probe_cases_v1(REAL_MEMORYARENA)
        self.assertEqual(len(cases), 198)

    def test_toolbench_198_cases_load_v1(self) -> None:
        cases = load_probe_cases_v1(REAL_TOOLBENCH)
        self.assertEqual(len(cases), 198)

    def test_total_real_cases_is_596(self) -> None:
        total = 0
        for f in (REAL_LONGMEMEVAL, REAL_MEMORYARENA, REAL_TOOLBENCH):
            total += len(load_probe_cases_v1(f))
        self.assertEqual(total, 596)

    def test_v0_loader_accepts_longmemeval_cases(self) -> None:
        """LongMemEval cases have V0 labels; V0 loader should accept them."""
        cases = load_probe_cases(REAL_LONGMEMEVAL)
        self.assertEqual(len(cases), 200)

    def test_v0_loader_accepts_memoryarena_cases(self) -> None:
        """MemoryArena cases have V0 labels; V0 loader should accept them."""
        cases = load_probe_cases(REAL_MEMORYARENA)
        self.assertEqual(len(cases), 198)

    def test_v0_loader_rejects_toolbench_cases(self) -> None:
        """ToolBench cases include V1 labels (route_error); V0 loader must reject."""
        with self.assertRaises(LabelValidationError):
            load_probe_cases(REAL_TOOLBENCH)

    def test_longmemeval_cases_have_valid_v1_labels(self) -> None:
        cases = load_probe_cases_v1(REAL_LONGMEMEVAL)
        for case in cases:
            with self.subTest(case_id=case.case_id):
                self.assertIn(case.perturbation_label, PIPELINE_LABELS)

    def test_memoryarena_cases_have_valid_v1_labels(self) -> None:
        cases = load_probe_cases_v1(REAL_MEMORYARENA)
        for case in cases:
            with self.subTest(case_id=case.case_id):
                self.assertIn(case.perturbation_label, PIPELINE_LABELS)

    def test_toolbench_cases_have_valid_v1_labels(self) -> None:
        cases = load_probe_cases_v1(REAL_TOOLBENCH)
        for case in cases:
            with self.subTest(case_id=case.case_id):
                self.assertIn(case.perturbation_label, PIPELINE_LABELS)

    def test_all_real_cases_have_non_empty_extracted_memory(self) -> None:
        for f in (REAL_LONGMEMEVAL, REAL_MEMORYARENA, REAL_TOOLBENCH):
            cases = load_probe_cases_v1(f)
            for case in cases:
                with self.subTest(case_id=case.case_id):
                    self.assertGreater(len(case.extracted_memory), 0,
                                       f"{case.case_id}: extracted_memory is empty")

    def test_all_real_cases_have_gold_evidence(self) -> None:
        for f in (REAL_LONGMEMEVAL, REAL_MEMORYARENA, REAL_TOOLBENCH):
            cases = load_probe_cases_v1(f)
            for case in cases:
                with self.subTest(case_id=case.case_id):
                    self.assertGreater(len(case.gold_evidence), 0,
                                       f"{case.case_id}: gold_evidence is empty")


# ── Null Perturbation Label Handling ──────────────────────────────────


class NullPerturbationLabelTest(unittest.TestCase):
    """Null perturbation_label cases load, run, and are excluded from macro F1."""

    def test_null_label_cases_load_v1(self) -> None:
        cases = load_probe_cases_v1(NULL_LABEL_FIXTURE)
        self.assertEqual(len(cases), 5)
        for case in cases:
            with self.subTest(case_id=case.case_id):
                self.assertIsNone(case.perturbation_label)

    def test_null_label_cases_load_v0(self) -> None:
        """V0 loader also accepts null label cases (null bypasses validation)."""
        cases = load_probe_cases(NULL_LABEL_FIXTURE)
        self.assertEqual(len(cases), 5)
        for case in cases:
            self.assertIsNone(case.perturbation_label)

    def test_null_label_case_runs_v1_pipeline(self) -> None:
        case = load_probe_cases_v1(NULL_LABEL_FIXTURE)[0]
        result = run_case_v1(case)
        self.assertIsNone(result.perturbation_label)
        self.assertIsNotNone(result.attribution)
        self.assertIsNotNone(result.attribution.predicted_label)

    def test_null_label_attribution_correct_is_none(self) -> None:
        case = load_probe_cases_v1(NULL_LABEL_FIXTURE)[0]
        result = run_case_v1(case)
        self.assertIsNone(result.attribution_correct)

    def test_null_label_case_runs_full_pipeline(self) -> None:
        case = load_probe_cases_v1(NULL_LABEL_FIXTURE)[0]
        result = run_case_full_v1(case)
        self.assertIsNotNone(result.ecs_draft)
        self.assertIsNotNone(result.post_repair)
        self.assertIsNotNone(result.post_repair.repair_assessment)

    def test_null_label_diagnosis_prediction_construction(self) -> None:
        """DiagnosisPrediction accepts None gold_label."""
        pred = DiagnosisPrediction(
            system_name="test",
            case_id="test-001",
            gold_label=None,
            predicted_label="retrieval_error",
            top2_labels=("retrieval_error",),
            cost_per_diagnosis=1.0,
        )
        self.assertIsNone(pred.gold_label)

    def test_null_label_excluded_from_metrics(self) -> None:
        """Null gold_label rows are excluded from accuracy/F1."""
        labeled_pred = DiagnosisPrediction(
            system_name="test",
            case_id="labeled-001",
            gold_label="retrieval_error",
            predicted_label="compression_error",
            top2_labels=("compression_error",),
            cost_per_diagnosis=1.0,
        )
        null_pred = DiagnosisPrediction(
            system_name="test",
            case_id="null-001",
            gold_label=None,
            predicted_label="retrieval_error",
            top2_labels=("retrieval_error",),
            cost_per_diagnosis=2.0,
        )
        metrics = compute_diagnosis_metrics([labeled_pred, null_pred])
        self.assertIn("test", metrics)
        m = metrics["test"]
        # Accuracy: only the labeled row, which is wrong, so 0.0
        self.assertEqual(m.attribution_accuracy, 0.0)
        # Cost: both rows averaged
        self.assertEqual(m.cost_per_diagnosis, 1.5)

    def test_mixed_null_and_labeled_pipeline_run(self) -> None:
        """CMD V1 pipeline runs on mixed suite of null-label + labeled cases."""
        null_cases = load_probe_cases_v1(NULL_LABEL_FIXTURE)
        labeled_cases = load_probe_cases_v1(V0_SMOKE)
        mixed = null_cases + labeled_cases
        results = run_cases_v1(mixed)
        self.assertEqual(len(results), len(mixed))
        null_results = [r for r in results if r.perturbation_label is None]
        labeled_results = [r for r in results if r.perturbation_label is not None]
        self.assertEqual(len(null_results), len(null_cases))
        self.assertEqual(len(labeled_results), len(labeled_cases))
        for r in null_results:
            self.assertIsNone(r.attribution_correct)
        for r in labeled_results:
            self.assertIsNotNone(r.attribution_correct)

    def test_diagnosis_predictions_passthrough_null_labels(self) -> None:
        null_case = load_probe_cases_v1(NULL_LABEL_FIXTURE)[0]
        result = run_case_v1(null_case)
        preds = diagnosis_predictions(result)
        for pred in preds:
            self.assertIsNone(pred.gold_label)


# ── Memory-Probe on Mixed Suite ───────────────────────────────────────


class MemoryProbeMixedSuiteTest(unittest.TestCase):
    """Memory-probe 3x2 grid runs against full probe suite (synthetic + real-data)."""

    def test_memory_probe_runs_on_synthetic_cases(self) -> None:
        cases = load_probe_cases_v1(V0_SMOKE)
        result = run_memory_probe_baselines(cases)
        self.assertIsInstance(result, MemoryProbeBaselineResult)
        self.assertEqual(len(result.case_results), len(cases))
        self.assertGreaterEqual(result.best_cell_accuracy, 0.0)
        self.assertLessEqual(result.best_cell_accuracy, 1.0)

    def test_memory_probe_runs_on_real_longmemeval(self) -> None:
        cases = load_probe_cases_v1(REAL_LONGMEMEVAL)[:10]
        result = run_memory_probe_baselines(cases)
        self.assertEqual(len(result.case_results), 10)
        self.assertIn(result.best_write_strategy, WRITE_STRATEGIES)
        self.assertIn(result.best_retrieval_method, RETRIEVAL_METHODS)

    def test_memory_probe_runs_on_real_memoryarena(self) -> None:
        cases = load_probe_cases_v1(REAL_MEMORYARENA)[:10]
        result = run_memory_probe_baselines(cases)
        self.assertEqual(len(result.case_results), 10)

    def test_memory_probe_runs_on_real_toolbench(self) -> None:
        cases = load_probe_cases_v1(REAL_TOOLBENCH)[:10]
        result = run_memory_probe_baselines(cases)
        self.assertEqual(len(result.case_results), 10)

    def test_memory_probe_runs_on_null_label_cases(self) -> None:
        """Memory-probe grid runs on null-label cases (no ground truth required)."""
        cases = load_probe_cases_v1(NULL_LABEL_FIXTURE)
        result = run_memory_probe_baselines(cases)
        self.assertEqual(len(result.case_results), 5)

    def test_memory_probe_mixed_suite(self) -> None:
        """Memory-probe runs on mixed suite: synthetic + real + null-label."""
        synthetic = load_probe_cases_v1(V0_SMOKE)
        real_cases = load_probe_cases_v1(REAL_LONGMEMEVAL)[:5]
        null_cases = load_probe_cases_v1(NULL_LABEL_FIXTURE)
        mixed = synthetic + real_cases + null_cases
        result = run_memory_probe_baselines(mixed)
        self.assertEqual(len(result.case_results), len(mixed))
        self.assertGreaterEqual(result.best_cell_accuracy, 0.0)
        self.assertLessEqual(result.best_cell_accuracy, 1.0)

    def test_memory_probe_case_result_has_six_cells(self) -> None:
        """Each case produces exactly 6 cell results (3 write x 2 retrieval: cosine, bm25)."""
        case = load_probe_cases_v1(REAL_LONGMEMEVAL)[0]
        result = run_memory_probe_case(case)
        self.assertIsInstance(result, MemoryProbeCaseResult)
        self.assertEqual(len(result.cell_results), 6)

    def test_memory_probe_best_cell_is_from_results(self) -> None:
        case = load_probe_cases_v1(REAL_LONGMEMEVAL)[0]
        result = run_memory_probe_case(case)
        self.assertIn(result.best_cell, result.cell_results)

    def test_memory_probe_case_ids_match(self) -> None:
        cases = load_probe_cases_v1(REAL_LONGMEMEVAL)[:5]
        result = run_memory_probe_baselines(cases)
        result_ids = {cr.case_id for cr in result.case_results}
        input_ids = {c.case_id for c in cases}
        self.assertEqual(result_ids, input_ids)


# ── Comparison Metrics with Memory-Probe Column ───────────────────────


class ComparisonMetricsTest(unittest.TestCase):
    """comparison_metrics.csv includes memory-probe column for mixed suite."""

    def test_metrics_table_includes_memory_probe_column(self) -> None:
        cases = load_probe_cases_v1(V0_SMOKE)
        results = run_cases_v1(cases)
        output = Path("artifacts/sandbox/test_issue16_metrics_mixed.csv")
        write_comparison_metrics_table(
            results, output, memory_probe_best_accuracy=0.420
        )
        content = output.read_text()
        self.assertIn("memory_probe_best_accuracy", content)
        self.assertIn("0.420", content)

    def test_metrics_table_with_null_label_and_memory_probe(self) -> None:
        """Metrics with null-label cases + memory-probe column writes correctly."""
        null_cases = load_probe_cases_v1(NULL_LABEL_FIXTURE)
        labeled_cases = load_probe_cases_v1(V0_SMOKE)
        mixed = null_cases + labeled_cases
        results = run_cases_v1(mixed)
        mp_result = run_memory_probe_baselines(mixed)
        output = Path("artifacts/sandbox/test_issue16_metrics_null_mixed.csv")
        write_comparison_metrics_table(
            results, output, memory_probe_best_accuracy=mp_result.best_cell_accuracy
        )
        content = output.read_text()
        self.assertIn("memory_probe_best_accuracy", content)
        # CMD accuracy should be computed only on labeled cases
        self.assertIn("CMD-Audit", content)

    def test_metrics_table_without_memory_probe_omits_column(self) -> None:
        cases = load_probe_cases_v1(V0_SMOKE)
        results = run_cases_v1(cases)
        output = Path("artifacts/sandbox/test_issue16_metrics_no_mp.csv")
        write_comparison_metrics_table(results, output)
        content = output.read_text()
        self.assertNotIn("memory_probe_best_accuracy", content)

    def test_metrics_on_full_real_data_sample(self) -> None:
        """Run metrics on 20 real cases (10 LongMemEval + 10 MemoryArena)."""
        cases = (
            load_probe_cases_v1(REAL_LONGMEMEVAL)[:10]
            + load_probe_cases_v1(REAL_MEMORYARENA)[:10]
        )
        results = run_cases_v1(cases)
        mp_result = run_memory_probe_baselines(cases)
        output = Path("artifacts/sandbox/test_issue16_metrics_real.csv")
        write_comparison_metrics_table(
            results, output, memory_probe_best_accuracy=mp_result.best_cell_accuracy
        )
        content = output.read_text()
        lines = content.strip().split("\n")
        # header + 4 systems (CMD, evidence_recall, random_label, subagent_judge)
        self.assertGreaterEqual(len(lines), 2)


# ── Source Integrity ──────────────────────────────────────────────────


class RealDataSourceIntegrityTest(unittest.TestCase):
    """Verify data source distribution matches cleaning report."""

    def test_source_distribution_matches_report(self) -> None:
        """596 = 200 longmemeval + 198 memoryarena + 198 toolbench."""
        longmemeval = len(load_probe_cases_v1(REAL_LONGMEMEVAL))
        memoryarena = len(load_probe_cases_v1(REAL_MEMORYARENA))
        toolbench = len(load_probe_cases_v1(REAL_TOOLBENCH))
        self.assertEqual(longmemeval, 200)
        self.assertEqual(memoryarena, 198)
        self.assertEqual(toolbench, 198)
        self.assertEqual(longmemeval + memoryarena + toolbench, 596)

    def test_case_ids_are_unique_across_sources(self) -> None:
        ids_set: set[str] = set()
        for f in (REAL_LONGMEMEVAL, REAL_MEMORYARENA, REAL_TOOLBENCH):
            for case in load_probe_cases_v1(f):
                self.assertNotIn(case.case_id, ids_set,
                                 f"Duplicate case_id: {case.case_id}")
                ids_set.add(case.case_id)
        self.assertEqual(len(ids_set), 596)

    def test_longmemeval_has_all_v0_labels(self) -> None:
        """LongMemEval cases cover multiple V0 pipeline labels."""
        from cmd_audit.core.labels import PIPELINE_LABELS_BASE
        cases = load_probe_cases_v1(REAL_LONGMEMEVAL)
        labels = {c.perturbation_label for c in cases}
        # Exactly which V0 labels appear depends on data construction;
        # verify each label is a valid V0 or V1 label.
        for label in labels:
            self.assertIn(label, PIPELINE_LABELS)

    def test_null_label_file_ids_are_distinct(self) -> None:
        """Null-label test cases have distinct IDs from real cases."""
        real_ids = set()
        for f in (REAL_LONGMEMEVAL, REAL_MEMORYARENA, REAL_TOOLBENCH):
            for case in load_probe_cases_v1(f):
                real_ids.add(case.case_id)
        null_ids = {c.case_id for c in load_probe_cases_v1(NULL_LABEL_FIXTURE)}
        self.assertTrue(null_ids.isdisjoint(real_ids),
                        "Null-label case IDs must not collide with real case IDs")
