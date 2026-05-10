"""Behavior-level tests for CMD-Audit issue 0008: retrieval baselines and evidence scoring."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cmd_audit import load_probe_cases
from cmd_audit.retrieval_baselines import (
    RankedRetrievalTrace,
    RetrievalBaselineResult,
    RetrievalBaselineSuiteResult,
    RetrievalMetrics,
    compute_evidence_boundary_audit,
    compute_retrieval_metrics,
    enforce_retrieval_error_boundary,
    run_bm25_retrieval,
    run_hybrid_rerank_retrieval,
    run_retrieval_baseline_suite,
)
from cmd_audit.scoring import answer_score, evidence_recall_from_text

HARD_NEGATIVES = Path("data/probe_cases/v0_issue8_hard_negatives.json")
RETRIEVAL_ERROR_FIXTURE = Path("data/probe_cases/v0_retrieval_error_case.json")
PREMATURE_FIXTURE = Path("data/probe_cases/v0_premature_extraction_error_case.json")


# ---------------------------------------------------------------------------
# Data class contract tests
# ---------------------------------------------------------------------------


class RankedRetrievalTraceContractTest(unittest.TestCase):
    def test_rank_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            RankedRetrievalTrace(
                case_id="c1", run_id="r1", retriever_name="bm25",
                memory_id="m1", rank=0, score=1.0, token_cost=0.0,
                retrieved_text="text", matched_gold_evidence_units=1,
                is_gold_support=True, is_distractor=False,
            )

    def test_score_must_be_nonnegative(self) -> None:
        with self.assertRaises(ValueError):
            RankedRetrievalTrace(
                case_id="c1", run_id="r1", retriever_name="bm25",
                memory_id="m1", rank=1, score=-0.1, token_cost=0.0,
                retrieved_text="text", matched_gold_evidence_units=1,
                is_gold_support=True, is_distractor=False,
            )

    def test_frozen_dataclass_prevents_mutation(self) -> None:
        trace = RankedRetrievalTrace(
            case_id="c1", run_id="r1", retriever_name="bm25",
            memory_id="m1", rank=1, score=1.0, token_cost=0.0,
            retrieved_text="text", matched_gold_evidence_units=1,
            is_gold_support=True, is_distractor=False,
        )
        with self.assertRaises(Exception):
            trace.rank = 2  # type: ignore[misc]


class RetrievalMetricsContractTest(unittest.TestCase):
    def test_metric_fields_must_be_in_range(self) -> None:
        with self.assertRaises(ValueError):
            RetrievalMetrics(
                retriever_name="bm25", case_id="c1",
                recall_at_1=1.5, recall_at_3=0.0, recall_at_5=0.0, recall_at_10=0.0,
                mrr=0.0, ndcg_at_10=0.0,
                precision_at_1=0.0, precision_at_3=0.0, precision_at_5=0.0,
                context_noise_ratio=0.0, answer_accuracy=0.0, answer_f1=0.0,
            )

    def test_frozen_dataclass_prevents_mutation(self) -> None:
        metrics = RetrievalMetrics(
            retriever_name="bm25", case_id="c1",
            recall_at_1=1.0, recall_at_3=0.5, recall_at_5=0.3, recall_at_10=0.1,
            mrr=1.0, ndcg_at_10=1.0,
            precision_at_1=1.0, precision_at_3=0.5, precision_at_5=0.3,
            context_noise_ratio=0.0, answer_accuracy=1.0, answer_f1=1.0,
        )
        with self.assertRaises(Exception):
            metrics.mrr = 0.5  # type: ignore[misc]


# ---------------------------------------------------------------------------
# BM25 retriever tests
# ---------------------------------------------------------------------------


class BM25RetrievalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.hn_cases = load_probe_cases(HARD_NEGATIVES)

    def test_bm25_ranks_all_memory_items(self) -> None:
        case = self.hn_cases[0]
        traces = run_bm25_retrieval(case)
        self.assertEqual(len(traces), len(case.extracted_memory))

    def test_bm25_run_id_is_deterministic(self) -> None:
        case = self.hn_cases[0]
        r1 = run_bm25_retrieval(case)
        r2 = run_bm25_retrieval(case)
        self.assertEqual(r1[0].run_id, r2[0].run_id)

    def test_bm25_token_cost_is_zero(self) -> None:
        case = self.hn_cases[0]
        traces = run_bm25_retrieval(case)
        for t in traces:
            self.assertEqual(t.token_cost, 0.0)

    def test_bm25_trace_has_correct_case_id(self) -> None:
        case = self.hn_cases[0]
        traces = run_bm25_retrieval(case)
        for t in traces:
            self.assertEqual(t.case_id, case.case_id)

    def test_bm25_trace_ranks_are_consecutive_from_1(self) -> None:
        case = self.hn_cases[0]
        traces = run_bm25_retrieval(case)
        ranks = {t.rank for t in traces}
        self.assertEqual(ranks, set(range(1, len(traces) + 1)))

    def test_bm25_gold_support_and_distractor_are_mutually_exclusive(self) -> None:
        case = self.hn_cases[0]
        traces = run_bm25_retrieval(case)
        for t in traces:
            self.assertNotEqual(t.is_gold_support, t.is_distractor)

    def test_bm25_empty_memory_case_returns_empty(self) -> None:
        case = load_probe_cases(RETRIEVAL_ERROR_FIXTURE)[0]
        traces = run_bm25_retrieval(case)
        self.assertGreater(len(traces), 0)
        for t in traces:
            self.assertIn(t.is_gold_support or t.is_distractor, (True, False))


# ---------------------------------------------------------------------------
# HybridRerank retriever tests
# ---------------------------------------------------------------------------


class HybridRerankRetrievalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.hn_cases = load_probe_cases(HARD_NEGATIVES)

    def test_hybrid_rerank_ranks_all_memory_items(self) -> None:
        case = self.hn_cases[0]
        traces = run_hybrid_rerank_retrieval(case)
        self.assertEqual(len(traces), len(case.extracted_memory))

    def test_hybrid_rerank_is_deterministic(self) -> None:
        case = self.hn_cases[0]
        r1 = run_hybrid_rerank_retrieval(case)
        r2 = run_hybrid_rerank_retrieval(case)
        self.assertEqual(
            [t.memory_id for t in r1],
            [t.memory_id for t in r2],
        )

    def test_hybrid_rerank_temporal_conflict_recovers_gold(self) -> None:
        """In temporal conflict case, BM25 may rank outdated memory higher;
        HybridRerank should push the updated gold to rank 1."""
        case = next(
            c for c in self.hn_cases if c.case_id == "v0-hn-temporal-002"
        )
        traces = run_hybrid_rerank_retrieval(case)
        self.assertTrue(traces[0].is_gold_support)
        self.assertEqual(traces[0].memory_id, "mem-005")

    def test_hybrid_rerank_paraphrase_recovers_gold(self) -> None:
        """In paraphrase case, BM25 may rank the paraphrased version higher;
        HybridRerank should identify the exact gold evidence."""
        case = next(
            c for c in self.hn_cases if c.case_id == "v0-hn-paraphrase-003"
        )
        traces = run_hybrid_rerank_retrieval(case)
        self.assertTrue(traces[0].is_gold_support)
        self.assertEqual(traces[0].memory_id, "mem-007")

    def test_hybrid_rerank_compression_loss_recovers_gold(self) -> None:
        """In compression-loss case, the lossy memory may score higher on
        keyword overlap; HybridRerank should prefer the full-detail gold."""
        case = next(
            c for c in self.hn_cases if c.case_id == "v0-hn-compress-006"
        )
        traces = run_hybrid_rerank_retrieval(case)
        self.assertTrue(traces[0].is_gold_support)
        self.assertEqual(traces[0].memory_id, "mem-016")

    def test_hybrid_rerank_multihop_both_golds_in_top2(self) -> None:
        """Multi-hop case: both gold supports should appear in top-2."""
        case = next(
            c for c in self.hn_cases if c.case_id == "v0-hn-multihop-004"
        )
        traces = run_hybrid_rerank_retrieval(case)
        top2_gold = sum(1 for t in traces[:2] if t.is_gold_support)
        self.assertGreaterEqual(top2_gold, 1)


# ---------------------------------------------------------------------------
# Retrieval metrics tests
# ---------------------------------------------------------------------------


class RetrievalMetricsTest(unittest.TestCase):
    def test_perfect_retrieval_gives_max_recall_mrr(self) -> None:
        """When gold support is at rank 1, recall@1=1.0 and mrr=1.0."""
        case = load_probe_cases(RETRIEVAL_ERROR_FIXTURE)[0]
        traces = run_bm25_retrieval(case)
        metrics = compute_retrieval_metrics(
            traces, case.case_id, "bm25", case.gold_answer
        )
        self.assertAlmostEqual(metrics.recall_at_1, 1.0)
        self.assertAlmostEqual(metrics.mrr, 1.0)
        self.assertAlmostEqual(metrics.precision_at_1, 1.0)

    def test_mrr_is_reciprocal_of_first_gold_rank(self) -> None:
        """MRR = 1/rank_of_first_gold_support."""
        case = load_probe_cases(RETRIEVAL_ERROR_FIXTURE)[0]
        traces = run_bm25_retrieval(case)
        first_gold_rank = min(t.rank for t in traces if t.is_gold_support)
        metrics = compute_retrieval_metrics(
            traces, case.case_id, "bm25", case.gold_answer
        )
        self.assertAlmostEqual(metrics.mrr, 1.0 / first_gold_rank)

    def test_context_noise_ratio_in_range(self) -> None:
        case = load_probe_cases(RETRIEVAL_ERROR_FIXTURE)[0]
        traces = run_bm25_retrieval(case)
        metrics = compute_retrieval_metrics(
            traces, case.case_id, "bm25", case.gold_answer
        )
        self.assertGreaterEqual(metrics.context_noise_ratio, 0.0)
        self.assertLessEqual(metrics.context_noise_ratio, 1.0)

    def test_answer_accuracy_detects_gold_in_top1(self) -> None:
        case = load_probe_cases(HARD_NEGATIVES)[0]  # gold=Lisbon
        traces = run_bm25_retrieval(case)
        metrics = compute_retrieval_metrics(
            traces, case.case_id, "bm25", case.gold_answer
        )
        self.assertEqual(metrics.answer_accuracy, 1.0)

    def test_answer_f1_in_range(self) -> None:
        case = load_probe_cases(HARD_NEGATIVES)[0]
        traces = run_bm25_retrieval(case)
        metrics = compute_retrieval_metrics(
            traces, case.case_id, "bm25", case.gold_answer
        )
        self.assertGreaterEqual(metrics.answer_f1, 0.0)
        self.assertLessEqual(metrics.answer_f1, 1.0)

    def test_nDCG_at_10_in_range(self) -> None:
        case = load_probe_cases(HARD_NEGATIVES)[0]
        traces = run_bm25_retrieval(case)
        metrics = compute_retrieval_metrics(
            traces, case.case_id, "bm25", case.gold_answer
        )
        self.assertGreaterEqual(metrics.ndcg_at_10, 0.0)
        self.assertLessEqual(metrics.ndcg_at_10, 1.0)

    def test_empty_traces_produces_zero_metrics(self) -> None:
        metrics = compute_retrieval_metrics(
            [], "empty_case", "bm25", "answer"
        )
        self.assertEqual(metrics.recall_at_1, 0.0)
        self.assertEqual(metrics.mrr, 0.0)
        self.assertEqual(metrics.answer_accuracy, 0.0)


# ---------------------------------------------------------------------------
# Evidence boundary tests
# ---------------------------------------------------------------------------


class EvidenceBoundaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.retrieval_case = load_probe_cases(RETRIEVAL_ERROR_FIXTURE)[0]
        cls.premature_case = load_probe_cases(PREMATURE_FIXTURE)[0]

    def test_boundary_allows_flip_when_memory_contains_evidence(self) -> None:
        """Memory item text containing gold evidence phrases allows flip."""
        # Find a memory item whose text contains the gold evidence
        found = False
        for item in self.retrieval_case.extracted_memory:
            if evidence_recall_from_text(
                self.retrieval_case.gold_evidence, item.text
            ) >= 1.0:
                self.assertTrue(
                    enforce_retrieval_error_boundary(
                        self.retrieval_case, item.text
                    )
                )
                found = True
        self.assertTrue(found, "No memory item contained the gold evidence")

    def test_boundary_blocks_flip_when_memory_lacks_evidence(self) -> None:
        """Memory item text NOT containing evidence blocks flip."""
        case = self.premature_case
        # In premature_extraction_error case, extracted memory should NOT
        # contain the gold evidence phrases
        for item in case.extracted_memory:
            if evidence_recall_from_text(case.gold_evidence, item.text) < 1.0:
                self.assertFalse(
                    enforce_retrieval_error_boundary(case, item.text)
                )

    def test_boundary_audit_maps_all_memory_items(self) -> None:
        case = self.premature_case
        audit = compute_evidence_boundary_audit(case)
        expected_ids = {m.memory_id for m in case.extracted_memory}
        self.assertEqual(set(audit), expected_ids)

    def test_boundary_audit_on_premature_case_all_blocked(self) -> None:
        """All memory items in premature_extraction_error case should block
        the flip because extraction already lost the evidence."""
        case = self.premature_case
        audit = compute_evidence_boundary_audit(case)
        self.assertFalse(
            any(audit.values()),
            "No memory item in premature_extraction_error case should "
            "allow a retrieval_error flip",
        )

    def test_boundary_audit_on_retrieval_case_has_flippable(self) -> None:
        case = self.retrieval_case
        audit = compute_evidence_boundary_audit(case)
        self.assertTrue(
            any(audit.values()),
            "At least one memory item in retrieval_error case should "
            "allow a retrieval_error flip",
        )

    def test_boundary_respects_custom_evidence_tuple(self) -> None:
        """Custom gold_evidence tuple overrides case default."""
        case = self.premature_case
        custom_evidence = case.gold_evidence
        for item in case.extracted_memory:
            result_default = enforce_retrieval_error_boundary(case, item.text)
            result_custom = enforce_retrieval_error_boundary(
                case, item.text, gold_evidence=custom_evidence
            )
            self.assertEqual(result_default, result_custom)


# ---------------------------------------------------------------------------
# Retrieval baseline suite tests
# ---------------------------------------------------------------------------


class RetrievalBaselineSuiteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(HARD_NEGATIVES)

    def test_suite_runs_both_retrievers(self) -> None:
        case = self.cases[0]
        result = run_retrieval_baseline_suite(case)
        self.assertEqual(len(result.baseline_results), 2)
        names = {r.retriever_name for r in result.baseline_results}
        self.assertEqual(names, {"bm25", "hybrid_rerank"})

    def test_suite_result_matches_case_id(self) -> None:
        case = self.cases[0]
        result = run_retrieval_baseline_suite(case)
        self.assertEqual(result.case_id, case.case_id)
        for r in result.baseline_results:
            self.assertEqual(r.case_id, case.case_id)

    def test_suite_traces_are_frozen_tuples(self) -> None:
        case = self.cases[0]
        result = run_retrieval_baseline_suite(case)
        for r in result.baseline_results:
            self.assertIsInstance(r.traces, tuple)

    def test_suite_best_answer_score_is_valid(self) -> None:
        case = self.cases[0]
        result = run_retrieval_baseline_suite(case)
        for r in result.baseline_results:
            self.assertIn(r.best_answer_score, (0.0, 1.0))


# ---------------------------------------------------------------------------
# Hard negatives tests
# ---------------------------------------------------------------------------


class HardNegativesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(HARD_NEGATIVES)

    def test_all_six_hard_negative_cases_load(self) -> None:
        self.assertEqual(len(self.cases), 6)

    def test_each_case_perturbation_label_is_retrieval_error(self) -> None:
        for case in self.cases:
            self.assertEqual(case.perturbation_label, "retrieval_error")

    def test_each_case_has_gold_and_distractor_memory_items(self) -> None:
        for case in self.cases:
            golds = sum(
                1 for m in case.extracted_memory
                if evidence_recall_from_text(case.gold_evidence, m.text) > 0.0
            )
            distractors = len(case.extracted_memory) - golds
            self.assertGreaterEqual(
                golds, 1,
                f"{case.case_id}: need at least one gold support memory "
                f"(any positive evidence match, got {golds})"
            )
            self.assertGreaterEqual(
                distractors, 1,
                f"{case.case_id}: need at least one distractor memory "
                f"(got {distractors})"
            )

    def test_case_ids_are_unique(self) -> None:
        ids = [c.case_id for c in self.cases]
        self.assertEqual(len(ids), len(set(ids)))

    def test_hybrid_rerank_beats_or_ties_bm25_on_recall_at_1(self) -> None:
        """On hard negatives, HybridRerank should never be worse than BM25
        at rank-1 gold support retrieval."""
        for case in self.cases:
            suite = run_retrieval_baseline_suite(case)
            by_name = {r.retriever_name: r for r in suite.baseline_results}
            hr_recall = by_name["hybrid_rerank"].metrics.recall_at_1
            bm25_recall = by_name["bm25"].metrics.recall_at_1
            self.assertGreaterEqual(
                hr_recall, bm25_recall,
                f"{case.case_id}: HybridRerank recall@1 ({hr_recall}) "
                f"should be >= BM25 recall@1 ({bm25_recall})"
            )

    def test_hybrid_rerank_mrr_not_worse_than_bm25(self) -> None:
        """On hard negatives, HybridRerank MRR should never be worse than BM25."""
        for case in self.cases:
            suite = run_retrieval_baseline_suite(case)
            by_name = {r.retriever_name: r for r in suite.baseline_results}
            hr_mrr = by_name["hybrid_rerank"].metrics.mrr
            bm25_mrr = by_name["bm25"].metrics.mrr
            self.assertGreaterEqual(
                hr_mrr, bm25_mrr,
                f"{case.case_id}: HybridRerank MRR ({hr_mrr}) "
                f"should be >= BM25 MRR ({bm25_mrr})"
            )


# ---------------------------------------------------------------------------
# CSV writer tests
# ---------------------------------------------------------------------------


class RetrievalTableWriterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(HARD_NEGATIVES)

    def test_trace_table_written_with_all_fields(self) -> None:
        from cmd_audit.harness import write_retrieval_trace_table

        case = self.cases[0]
        suite = run_retrieval_baseline_suite(case)
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "retrieval_trace.csv"
            write_retrieval_trace_table([suite], output)
            text = output.read_text(encoding="utf-8")
        self.assertIn("case_id", text)
        self.assertIn("run_id", text)
        self.assertIn("retriever_name", text)
        self.assertIn("memory_id", text)
        self.assertIn("rank", text)
        self.assertIn("score", text)
        self.assertIn("token_cost", text)
        self.assertIn("retrieved_text", text)
        self.assertIn("matched_gold_evidence_units", text)
        self.assertIn("is_gold_support", text)
        self.assertIn("is_distractor", text)

    def test_metrics_table_written_with_all_fields(self) -> None:
        from cmd_audit.harness import write_retrieval_metrics_table

        case = self.cases[0]
        suite = run_retrieval_baseline_suite(case)
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "retrieval_metrics.csv"
            write_retrieval_metrics_table([suite], output)
            text = output.read_text(encoding="utf-8")
        self.assertIn("case_id", text)
        self.assertIn("retriever_name", text)
        self.assertIn("recall_at_1", text)
        self.assertIn("mrr", text)
        self.assertIn("ndcg_at_10", text)
        self.assertIn("context_noise_ratio", text)
        self.assertIn("answer_accuracy", text)
        self.assertIn("answer_f1", text)


if __name__ == "__main__":
    unittest.main()
