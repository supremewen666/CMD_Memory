"""Behavior-level tests for issue 0013: coupled-failure recalibration + memory-probe baseline."""

from __future__ import annotations

import csv
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from cmd_audit import (
    PIPELINE_LABELS,
    assign_attribution,
    assign_attribution_v1,
    load_probe_cases,
    load_probe_cases_v1,
    run_case,
    run_case_v1,
    run_memory_probe_baselines,
    run_memory_probe_case,
    write_attribution_table,
    write_comparison_metrics_table,
)
from cmd_audit.baselines.memory_probe import (
    WRITE_STRATEGIES,
    RETRIEVAL_METHODS,
    _write_fact_extraction,
    _write_raw_chunks,
    _write_summarization,
)
from cmd_audit.replays import ReplayResult
from cmd_audit.scoring import (
    tokenize,
    compute_bm25_scores,
    build_tfidf_vectors,
    cosine_similarity,
)

V0_SMOKE = Path("data/probe_cases/v0_issue3_cases.json")
GRANULARITY_FIXTURE = Path("data/probe_cases/v1_granularity_error_case.json")


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_replay(name: str, gain: float) -> ReplayResult:
    """Build a minimal ReplayResult for attribution testing."""
    return ReplayResult(
        replay_name=name,
        answer="dummy",
        answer_score=gain,
        evidence_score=0.0,
        evidence_block="",
        recovery_gain=gain,
    )


# ── AttributionResult Schema ─────────────────────────────────────────────


class AttributionResultSchemaTest(unittest.TestCase):
    """Verify the new top_k_labels and close_deltas fields on AttributionResult."""

    def test_default_fields_are_present(self) -> None:
        result = assign_attribution(
            (_make_replay("oracle_write", 1.0),),
            use_extended_labels=False,
            separate_reasoning_axis=False,
        )
        self.assertEqual(result.top_k_labels, result.top2_labels)
        self.assertEqual(result.close_deltas, ())

    def test_top_k_labels_is_tuple_of_strings(self) -> None:
        result = assign_attribution_v1((_make_replay("oracle_retrieval", 1.0),))
        self.assertIsInstance(result.top_k_labels, tuple)
        self.assertTrue(all(isinstance(label, str) for label in result.top_k_labels))

    def test_close_deltas_is_tuple_of_pairs(self) -> None:
        result = assign_attribution_v1((_make_replay("oracle_retrieval", 1.0),))
        self.assertIsInstance(result.close_deltas, tuple)

    def test_has_ingestion_trace_defaults(self) -> None:
        """top_k_labels / close_deltas respect ingestion/write split."""
        result = assign_attribution_v1(
            (
                _make_replay("oracle_write", 1.0),
                _make_replay("oracle_compression", 0.97),
            ),
            has_ingestion_trace=True,
        )
        self.assertEqual(result.predicted_label, "write_error")
        self.assertIn("write_error", result.top_k_labels)

        result2 = assign_attribution_v1(
            (
                _make_replay("oracle_write", 1.0),
                _make_replay("oracle_compression", 0.97),
            ),
            has_ingestion_trace=False,
        )
        self.assertEqual(result2.predicted_label, "ingestion_error")


# ── Top-K Attribution ────────────────────────────────────────────────────


class TopKAttributionTest(unittest.TestCase):
    """top_k=3 produces up to 3 labels when deltas are close."""

    def test_top3_with_three_close_deltas(self) -> None:
        replays = (
            _make_replay("oracle_retrieval", 1.0),
            _make_replay("oracle_compression", 0.98),
            _make_replay("oracle_write", 0.97),
            _make_replay("verbatim_event_oracle", 0.50),
        )
        result = assign_attribution_v1(replays, top_k=3, tie_margin=0.05)
        self.assertEqual(len(result.top_k_labels), 3)
        self.assertEqual(len(result.top2_labels), 2)
        self.assertTrue(result.is_ambiguous)

    def test_top3_with_two_close_deltas(self) -> None:
        replays = (
            _make_replay("oracle_retrieval", 1.0),
            _make_replay("oracle_compression", 0.98),
            _make_replay("oracle_write", 0.50),
        )
        result = assign_attribution_v1(replays, top_k=3, tie_margin=0.05)
        self.assertEqual(len(result.top_k_labels), 2)
        self.assertTrue(result.is_ambiguous)

    def test_top3_single_dominant(self) -> None:
        replays = (
            _make_replay("oracle_retrieval", 1.0),
            _make_replay("oracle_compression", 0.80),
        )
        result = assign_attribution_v1(replays, top_k=3, tie_margin=0.05)
        self.assertEqual(len(result.top_k_labels), 1)
        self.assertFalse(result.is_ambiguous)

    def test_default_top_k_is_2(self) -> None:
        replays = (
            _make_replay("oracle_retrieval", 1.0),
            _make_replay("oracle_compression", 0.98),
            _make_replay("oracle_write", 0.97),
        )
        result = assign_attribution_v1(replays)
        self.assertLessEqual(len(result.top_k_labels), 2)


# ── Coupled-Failure Edge Cases ───────────────────────────────────────────


class CoupledFailureEdgeCaseTest(unittest.TestCase):
    """Edge cases for close deltas with top_k=3."""

    def test_four_close_deltas_with_top_k_3(self) -> None:
        replays = (
            _make_replay("oracle_retrieval", 1.00),
            _make_replay("oracle_compression", 0.99),
            _make_replay("oracle_write", 0.98),
            _make_replay("oracle_granularity", 0.97),
            _make_replay("verbatim_event_oracle", 0.50),
        )
        result = assign_attribution_v1(replays, top_k=3, tie_margin=0.05)
        self.assertEqual(len(result.top_k_labels), 3, "top_k_labels capped at 3")
        self.assertEqual(len(result.top2_labels), 2, "top2_labels always capped at 2")
        self.assertGreaterEqual(
            len(result.close_deltas), 4, "close_deltas exposes all 4 unbounded"
        )
        self.assertTrue(result.is_ambiguous)

    def test_close_deltas_contain_correct_pairs(self) -> None:
        replays = (
            _make_replay("oracle_retrieval", 1.00),
            _make_replay("oracle_compression", 0.98),
        )
        result = assign_attribution_v1(replays, tie_margin=0.05)
        self.assertEqual(len(result.close_deltas), 2)
        labels = {label for label, _ in result.close_deltas}
        self.assertIn("retrieval_error", labels)
        self.assertIn("compression_error", labels)
        # First delta should be 0.0 (top replay itself)
        self.assertEqual(result.close_deltas[0][1], 0.0)

    def test_delta_beyond_threshold_excluded(self) -> None:
        replays = (
            _make_replay("oracle_retrieval", 1.00),
            _make_replay("oracle_compression", 0.94),
        )
        result = assign_attribution_v1(replays, tie_margin=0.05)
        self.assertEqual(len(result.close_deltas), 1, "second delta 0.06 > 0.05 margin")

    def test_top_k_labels_all_valid_v1(self) -> None:
        replays = (
            _make_replay("oracle_retrieval", 1.00),
            _make_replay("oracle_compression", 0.99),
            _make_replay("oracle_write", 0.98),
        )
        result = assign_attribution_v1(replays, top_k=3, tie_margin=0.05)
        for label in result.top_k_labels:
            self.assertIn(label, PIPELINE_LABELS)


# ── V0 Backward Compatibility ────────────────────────────────────────────


class V0BackwardCompatTest(unittest.TestCase):
    """V0 assign_attribution unchanged; legacy V1 fixture margin preserves behavior."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.v0_cases = load_probe_cases(V0_SMOKE)

    def test_v0_attribution_top_k_labels_matches_top2(self) -> None:
        for case in self.v0_cases:
            with self.subTest(case_id=case.case_id):
                result = run_case(case)
                self.assertEqual(
                    result.attribution.top_k_labels,
                    result.attribution.top2_labels,
                )
                self.assertEqual(result.attribution.close_deltas, ())

    def test_v0_cases_through_v1_with_default_top_k(self) -> None:
        for case in self.v0_cases:
            with self.subTest(case_id=case.case_id):
                v0_result = run_case(case)
                v1_result = run_case_v1(case, top_k=2, tie_margin=0.05)
                self.assertEqual(
                    v0_result.attribution.predicted_label,
                    v1_result.attribution.predicted_label,
                    f"V0 label {v0_result.attribution.predicted_label} != "
                    f"V1 label {v1_result.attribution.predicted_label}",
                )
                self.assertEqual(
                    v1_result.attribution.top_k_labels,
                    v1_result.attribution.top2_labels,
                )

    def test_v0_attribution_result_has_new_fields(self) -> None:
        result = assign_attribution(
            (_make_replay("oracle_write", 1.0),),
            use_extended_labels=False,
            separate_reasoning_axis=False,
        )
        self.assertEqual(result.top_k_labels, ("write_error",))
        self.assertEqual(result.close_deltas, ())


# ── Memory-Probe Write Strategies ────────────────────────────────────────


class MemoryProbeWriteStrategiesTest(unittest.TestCase):
    """Each write strategy produces non-empty, well-formed memory items."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases_v1(GRANULARITY_FIXTURE)
        cls.case = cls.cases[0]

    def test_fact_extraction_produces_items(self) -> None:
        items = _write_fact_extraction(self.case)
        self.assertGreater(len(items), 0)
        for item in items:
            self.assertTrue(item.memory_id.startswith("fact_"))
            self.assertGreater(len(item.text), 0)
            self.assertTrue(item.source_event_ids)

    def test_summarization_produces_items(self) -> None:
        items = _write_summarization(self.case)
        self.assertGreater(len(items), 0)
        for item in items:
            self.assertTrue(item.memory_id.startswith("summary_"))
            self.assertGreater(len(item.text), 0)

    def test_raw_chunks_produces_one_per_event(self) -> None:
        items = _write_raw_chunks(self.case)
        self.assertEqual(len(items), len(self.case.raw_events))
        for item, event in zip(items, self.case.raw_events):
            self.assertEqual(item.text, event.text)
            self.assertTrue(item.memory_id.startswith("raw_"))

    def test_all_strategies_return_memory_items(self) -> None:
        for name, fn in [
            ("fact_extraction", _write_fact_extraction),
            ("summarization", _write_summarization),
            ("raw_chunks", _write_raw_chunks),
        ]:
            with self.subTest(strategy=name):
                items = fn(self.case)
                self.assertIsInstance(items, tuple)
                self.assertGreater(len(items), 0)


# ── Memory-Probe Grid ────────────────────────────────────────────────────


class MemoryProbeGridTest(unittest.TestCase):
    """3x2 grid produces 6 cell results per case (dense retrieval deferred to V1 per issue 0008)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases_v1(GRANULARITY_FIXTURE)
        cls.case_result = run_memory_probe_case(cls.cases[0])

    def test_six_cells_produced(self) -> None:
        self.assertEqual(len(self.case_result.cell_results), 6)

    def test_each_cell_has_valid_strategy_and_method(self) -> None:
        for cell in self.case_result.cell_results:
            with self.subTest(ws=cell.write_strategy, rm=cell.retrieval_method):
                self.assertIn(cell.write_strategy, WRITE_STRATEGIES)
                self.assertIn(cell.retrieval_method, RETRIEVAL_METHODS)

    def test_scores_in_valid_range(self) -> None:
        for cell in self.case_result.cell_results:
            with self.subTest(ws=cell.write_strategy, rm=cell.retrieval_method):
                self.assertGreaterEqual(cell.answer_score, 0.0)
                self.assertLessEqual(cell.answer_score, 1.0)
                self.assertGreaterEqual(cell.evidence_score, 0.0)
                self.assertLessEqual(cell.evidence_score, 1.0)

    def test_best_cell_is_one_of_the_cells(self) -> None:
        self.assertIn(self.case_result.best_cell, self.case_result.cell_results)

    def test_case_id_matches(self) -> None:
        self.assertEqual(self.case_result.case_id, self.cases[0].case_id)

    def test_all_write_retrieval_combinations_covered(self) -> None:
        pairs = {
            (c.write_strategy, c.retrieval_method)
            for c in self.case_result.cell_results
        }
        self.assertEqual(
            len(pairs), 6, "all 6 (write x retrieval) pairs should be unique"
        )


# ── Memory-Probe Baseline ────────────────────────────────────────────────


class MemoryProbeBaselineTest(unittest.TestCase):
    """Aggregate baseline computes valid best_cell_accuracy."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.v0_cases = load_probe_cases(V0_SMOKE)
        cls.baseline = run_memory_probe_baselines(list(cls.v0_cases))

    def test_best_cell_accuracy_in_range(self) -> None:
        self.assertGreaterEqual(self.baseline.best_cell_accuracy, 0.0)
        self.assertLessEqual(self.baseline.best_cell_accuracy, 1.0)

    def test_best_strategy_and_method_are_valid(self) -> None:
        self.assertIn(self.baseline.best_write_strategy, WRITE_STRATEGIES)
        self.assertIn(self.baseline.best_retrieval_method, RETRIEVAL_METHODS)

    def test_case_results_count_matches_input(self) -> None:
        self.assertEqual(len(self.baseline.case_results), len(self.v0_cases))

    def test_each_case_result_has_six_cells(self) -> None:
        for cr in self.baseline.case_results:
            self.assertEqual(len(cr.cell_results), 6)

    def test_is_frozen_dataclass(self) -> None:
        with self.assertRaises(Exception):
            self.baseline.best_cell_accuracy = 0.5  # type: ignore


# ── Comparison Metrics with Memory-Probe Column ──────────────────────────


class ComparisonMetricsWithMemoryProbeTest(unittest.TestCase):
    """CSV includes memory_probe_best_accuracy column when provided."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(V0_SMOKE)
        cls.results = [run_case(case) for case in cls.cases]

    def test_memory_probe_column_present_when_provided(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "comparison_metrics.csv"
            write_comparison_metrics_table(
                self.results,
                path,
                memory_probe_best_accuracy=0.750,
            )
            content = path.read_text()
            self.assertIn("memory_probe_best_accuracy", content)

    def test_memory_probe_column_absent_when_none(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "comparison_metrics.csv"
            write_comparison_metrics_table(self.results, path)
            content = path.read_text()
            self.assertNotIn("memory_probe_best_accuracy", content)

    def test_memory_probe_value_in_every_data_row(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "comparison_metrics.csv"
            write_comparison_metrics_table(
                self.results,
                path,
                memory_probe_best_accuracy=0.750,
            )
            reader = csv.DictReader(path.open())
            for row in reader:
                self.assertIn("memory_probe_best_accuracy", row)
                self.assertEqual(row["memory_probe_best_accuracy"], "0.750")

    def test_memory_probe_value_parseable_as_float(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "comparison_metrics.csv"
            write_comparison_metrics_table(
                self.results,
                path,
                memory_probe_best_accuracy=0.833,
            )
            reader = csv.DictReader(path.open())
            for row in reader:
                val = float(row["memory_probe_best_accuracy"])
                self.assertAlmostEqual(val, 0.833, places=3)


# ── Attribution Table New Columns ────────────────────────────────────────


class AttributionTableNewColumnsTest(unittest.TestCase):
    """Attribution table CSV includes top_k_labels and close_deltas columns."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(V0_SMOKE)
        cls.results = [run_case(case) for case in cls.cases]
        cls.v1_results = [
            run_case_v1(case, top_k=3)
            for case in load_probe_cases_v1(GRANULARITY_FIXTURE)
        ]

    def test_new_columns_in_header(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "attribution_table.csv"
            write_attribution_table(list(self.results), path)
            content = path.read_text()
            self.assertIn("top_k_labels", content)
            self.assertIn("close_deltas", content)

    def test_v0_top_k_labels_matches_top2(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "attribution_table.csv"
            write_attribution_table(list(self.results), path)
            reader = csv.DictReader(path.open())
            for row in reader:
                self.assertEqual(row["top_k_labels"], row["top2_labels"])

    def test_v0_close_deltas_is_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "attribution_table.csv"
            write_attribution_table(list(self.results), path)
            reader = csv.DictReader(path.open())
            for row in reader:
                self.assertEqual(row["close_deltas"], "")

    def test_v1_close_deltas_has_format(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "attribution_table.csv"
            write_attribution_table(list(self.v1_results), path)
            reader = csv.DictReader(path.open())
            for row in reader:
                if row["close_deltas"]:
                    for pair in row["close_deltas"].split("|"):
                        label, delta_str = pair.split(":")
                        self.assertIn(label, PIPELINE_LABELS)
                        float(delta_str)  # parseable


# ── Retrieved Helpers are Public ─────────────────────────────────────────


class RetrievedHelpersPublicTest(unittest.TestCase):
    """Formerly-private retrieval_baselines helpers are importable and functional."""

    def test_tokenize_is_callable(self) -> None:
        tokens = tokenize("hello world test")
        self.assertIsInstance(tokens, list)
        self.assertTrue(all(isinstance(t, str) for t in tokens))

    def test_compute_bm25_scores_is_callable(self) -> None:
        scores = compute_bm25_scores(["hello"], [["hello", "world"], ["foo", "bar"]])
        self.assertEqual(len(scores), 2)
        self.assertGreater(scores[0], scores[1])

    def test_build_tfidf_vectors_is_callable(self) -> None:
        from cmd_audit.core.models import MemoryItem

        items = [
            MemoryItem(memory_id="a", text="hello world", store="episodic"),
            MemoryItem(memory_id="b", text="foo bar", store="episodic"),
        ]
        qv, dvs = build_tfidf_vectors(items, "hello")
        self.assertIsInstance(qv, dict)
        self.assertEqual(len(dvs), 2)

    def test_cosine_similarity_is_callable(self) -> None:
        sim = cosine_similarity({"a": 1.0}, {"a": 1.0})
        self.assertGreater(sim, 0.9)
        sim2 = cosine_similarity({"a": 1.0}, {"b": 1.0})
        self.assertEqual(sim2, 0.0)
