"""Tests for the two-branch confidence gate (DISCUSSION.md decision #5)."""

import unittest
from unittest.mock import Mock, patch

from cmd_audit.core.models import MemoryItem, RetrievedItem
from cmd_audit.hook import (
    CONFIDENCE_FACTOR_NAMES,
    FILL_FIX_THRESHOLD,
    ConfidenceFactors,
    HookDecision,
    compute_confidence_factors,
    post_retrieve_hook,
)
from cmd_audit.hook.confidence_gate import confidence_gate_hook
from cmd_audit.hook.router import route_fix_branch
from cmd_audit.hook.subagent_loop import SubagentLoopOrchestrator
from cmd_audit.item_gate import ItemGateResult, ItemGateStatus


def _items(*texts: str) -> tuple[RetrievedItem, ...]:
    return tuple(
        RetrievedItem(memory_id=f"mem-{idx:03d}", text=text)
        for idx, text in enumerate(texts)
    )


class TestConfidenceFactors(unittest.TestCase):
    def test_factor_count_is_6(self) -> None:
        self.assertEqual(len(CONFIDENCE_FACTOR_NAMES), 6)

    def test_empty_items_returns_zero_factors(self) -> None:
        factors = compute_confidence_factors("query", ())
        self.assertEqual(factors.as_tuple(), (0.0, 0.0, 0.0, 0.0, 0.0, 0.0))

    def test_as_tuple_returns_6_values(self) -> None:
        factors = compute_confidence_factors("Kai Madrid", _items("Kai chose Madrid"))
        self.assertEqual(len(factors.as_tuple()), 6)

    def test_high_coverage_query(self) -> None:
        factors = compute_confidence_factors(
            "Kai workshop city",
            _items("Kai chose Madrid for the partner workshop in the city center"),
        )
        self.assertGreater(factors.evidence_coverage, 0.3)

    def test_conflict_detection(self) -> None:
        factors = compute_confidence_factors(
            "workshop location",
            _items(
                "The workshop is in Madrid city center",
                "The workshop is not in Madrid city center",
            ),
        )
        self.assertEqual(factors.conflict_signal, 1.0)

    def test_no_conflict_different_topics(self) -> None:
        factors = compute_confidence_factors(
            "workshop location",
            _items("The workshop is in Madrid", "The conference is in Berlin"),
        )
        self.assertEqual(factors.conflict_signal, 0.0)


class TestHookDecision(unittest.TestCase):
    def test_valid_fill_branch(self) -> None:
        factors = ConfidenceFactors(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        decision = HookDecision(branch="fill", confidence=0.3, factors=factors)
        self.assertEqual(decision.branch, "fill")
        self.assertFalse(decision.trigger_diagnosis)

    def test_valid_fix_branch(self) -> None:
        factors = ConfidenceFactors(0.8, 0.2, 0.9, 0.0, 0.0, 0.0)
        decision = HookDecision(branch="fix", confidence=0.85, factors=factors)
        self.assertEqual(decision.branch, "fix")
        self.assertTrue(decision.trigger_diagnosis)

    def test_invalid_branch_rejected(self) -> None:
        factors = ConfidenceFactors(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        with self.assertRaises(ValueError):
            HookDecision(branch="skip", confidence=0.5, factors=factors)

    def test_invalid_confidence_rejected(self) -> None:
        factors = ConfidenceFactors(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        with self.assertRaises(ValueError):
            HookDecision(branch="fill", confidence=1.5, factors=factors)


class TestPostRetrieveHook(unittest.TestCase):
    def test_empty_items_returns_fill(self) -> None:
        decision = post_retrieve_hook("query", ())
        self.assertEqual(decision.branch, "fill")

    def test_high_quality_retrieval_returns_fix(self) -> None:
        decision = post_retrieve_hook(
            "Kai workshop city Madrid",
            _items("Kai chose Madrid for the partner workshop in the city"),
        )
        self.assertEqual(decision.branch, "fix")

    def test_factors_populated(self) -> None:
        decision = post_retrieve_hook("test query", _items("test document"))
        self.assertIsInstance(decision.factors, ConfidenceFactors)
        self.assertEqual(len(decision.factors.as_tuple()), 6)

    def test_failure_memory_bonus_can_raise_confidence_to_fix(self) -> None:
        class FakeStore:
            def get_hook_confidence_bonus(self, query):
                return 1.0

        decision = post_retrieve_hook(
            "query",
            (),
            failure_memory_store=FakeStore(),
        )

        self.assertEqual(decision.branch, "fix")
        self.assertEqual(decision.experience_bonus, 1.0)


class TestConfidenceGateWrapper(unittest.TestCase):
    def test_confidence_gate_reuses_canonical_factor_schema(self) -> None:
        result = confidence_gate_hook(
            "Kai workshop city Madrid",
            (
                MemoryItem(
                    "mem-001",
                    "Kai chose Madrid for the partner workshop in the city",
                ),
            ),
        )

        self.assertIsInstance(result.factors, ConfidenceFactors)
        self.assertEqual(result.branch, "fix")
        self.assertTrue(result.trigger_subagent_loop)


class TestRouter(unittest.TestCase):
    def test_route_fix_branch_reads_factors_not_missing_factor_scores(self) -> None:
        decision = HookDecision(
            branch="fix",
            confidence=0.9,
            factors=ConfidenceFactors(
                retrieval_score_max=0.4,
                retrieval_score_entropy=0.0,
                evidence_coverage=0.6,
                memory_recency_min=0.0,
                memory_recency_spread=0.0,
                conflict_signal=0.0,
            ),
        )

        result = route_fix_branch(
            "Madrid workshop",
            [
                MemoryItem("berlin", "Berlin conference notes"),
                MemoryItem("madrid", "Madrid workshop notes"),
            ],
            decision,
        )

        self.assertTrue(result.proceed_to_diagnosis)
        self.assertEqual(result.fixed_items[0].memory_id, "madrid")


class TestSubagentLoop(unittest.TestCase):
    def test_item_gate_runs_each_recalled_item_before_mcts(self) -> None:
        recall_set = (
            MemoryItem("clean", "Kai chose Madrid for the workshop"),
            MemoryItem("wrong", "Kai chose Berlin for the workshop"),
        )
        pass_result = ItemGateResult(
            target_item=recall_set[0],
            recall_set=recall_set,
            query="Kai workshop city",
            status=ItemGateStatus.PASS,
            collision_results=[],
            has_timestamp_conflicts=False,
            loo_result=None,
            processing_cost=1,
            decision_path="pass",
        )
        wrong_result = ItemGateResult(
            target_item=recall_set[1],
            recall_set=recall_set,
            query="Kai workshop city",
            status=ItemGateStatus.ITEM_WRONG,
            collision_results=[],
            has_timestamp_conflicts=False,
            loo_result=None,
            processing_cost=1,
            decision_path="wrong",
        )

        with (
            patch(
                "cmd_audit.hook.subagent_loop.run_item_gate",
                side_effect=[pass_result, wrong_result],
            ) as mock_item_gate,
            patch("cmd_audit.hook.subagent_loop.run_mcts_attribution") as mock_mcts,
        ):
            result = SubagentLoopOrchestrator().run_subagent_loop(
                "Kai workshop city",
                recall_set,
                Mock(),
            )

        self.assertEqual(mock_item_gate.call_count, 2)
        mock_mcts.assert_not_called()
        self.assertTrue(result.item_treatment_needed)
        self.assertEqual(result.primary_label, "item_wrong")


class TestThreshold(unittest.TestCase):
    def test_threshold_in_valid_range(self) -> None:
        self.assertGreater(FILL_FIX_THRESHOLD, 0.0)
        self.assertLess(FILL_FIX_THRESHOLD, 1.0)


if __name__ == "__main__":
    unittest.main()
