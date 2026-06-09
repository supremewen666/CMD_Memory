"""Tests for Tier 2 Item Gate implementation."""

import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timedelta

from cmd_audit.core.models import MemoryItem
from cmd_audit.item_gate import (
    DirectedDivergence,
    ItemGateResult,
    ItemGateStatus,
    compute_directed_divergence,
    detect_item_collision,
    compute_recall_set_divergence,
    leave_one_out_reconstruct,
    compute_loo_divergence,
    order_items_by_experience,
    run_item_gate,
    run_item_gate_for_recall_set,
)
from cmd_audit.item_gate.collision import CollisionResult
from cmd_audit.item_gate.loo import LOOReconstructionResult


class TestDirectedDivergence(unittest.TestCase):
    """Tests for directed entailment divergence computation."""

    def setUp(self):
        self.mock_client = Mock()
        self.item_a = MemoryItem(
            memory_id="item_a",
            text="Paris is the capital of France"
        )
        self.item_b = MemoryItem(
            memory_id="item_b",
            text="Paris is a major European city in France"
        )

    @patch('cmd_audit.item_gate.divergence._continuous_verify')
    def test_compute_directed_divergence_success(self, mock_continuous):
        """Test successful divergence computation with logprobs."""
        # Mock continuous_verify to return different scores for each direction
        mock_continuous.side_effect = [3.2, 2.1]  # forward, reverse

        divergence = compute_directed_divergence(self.mock_client, self.item_a, self.item_b)

        self.assertIsInstance(divergence, DirectedDivergence)
        self.assertEqual(divergence.forward_score, 3.2)
        self.assertEqual(divergence.reverse_score, 2.1)
        self.assertAlmostEqual(divergence.forward_divergence, 1.0 - (3.2 / 4.0))
        self.assertAlmostEqual(divergence.reverse_divergence, 1.0 - (2.1 / 4.0))
        self.assertFalse(divergence.is_forward_dominant)
        self.assertTrue(divergence.is_reverse_dominant)

    @patch('cmd_audit.item_gate.divergence._continuous_verify')
    def test_compute_directed_divergence_fallback(self, mock_continuous):
        """Test conservative fallback when continuous scoring fails."""
        mock_continuous.return_value = None  # Simulate logprobs unavailable

        divergence = compute_directed_divergence(
            self.mock_client, self.item_a, self.item_b, fallback_threshold=0.6
        )

        self.assertEqual(divergence.forward_score, 0.0)
        self.assertEqual(divergence.reverse_score, 0.0)
        self.assertEqual(divergence.max_divergence, 1.0)

    def test_compute_directed_divergence_no_client_is_not_pass(self):
        """No judge client must not collapse to zero divergence/PASS."""
        divergence = compute_directed_divergence(None, self.item_a, self.item_b)

        self.assertEqual(divergence.forward_score, 0.0)
        self.assertEqual(divergence.reverse_score, 0.0)
        self.assertEqual(divergence.max_divergence, 1.0)

    def test_divergence_properties(self):
        """Test DirectedDivergence property methods."""
        divergence = DirectedDivergence(
            forward_score=3.0, reverse_score=1.0,
            forward_divergence=0.75, reverse_divergence=0.25
        )

        self.assertEqual(divergence.max_divergence, 0.75)
        self.assertTrue(divergence.is_forward_dominant)
        self.assertFalse(divergence.is_reverse_dominant)


class TestCollisionDetection(unittest.TestCase):
    """Tests for recall-set collision detection."""

    def setUp(self):
        self.mock_client = Mock()

        # Items with different timestamps (using store field to simulate metadata)
        self.item_old = MemoryItem(
            memory_id="old_item",
            text="Paris is a city",
            store="2024-01-01T10:00:00Z"  # Using store field to simulate timestamp
        )
        self.item_new = MemoryItem(
            memory_id="new_item",
            text="Paris is the capital of France",
            store="2024-01-10T10:00:00Z"  # Using store field to simulate timestamp
        )

        # Items with same period timestamps
        self.item_same1 = MemoryItem(
            memory_id="same1",
            text="Temperature is 20°C",
            store="2024-01-01T10:00:00Z"
        )
        self.item_same2 = MemoryItem(
            memory_id="same2",
            text="Temperature is 25°C",
            store="2024-01-03T10:00:00Z"
        )

    @patch('cmd_audit.item_gate.collision.compute_directed_divergence')
    def test_detect_stale_collision(self, mock_divergence):
        """Test detection of stale collision (one item newer)."""
        # Mock high divergence
        mock_divergence.return_value = DirectedDivergence(
            forward_score=3.5, reverse_score=1.0,
            forward_divergence=0.875, reverse_divergence=0.25
        )

        collision = detect_item_collision(
            self.mock_client, self.item_old, self.item_new,
            divergence_threshold=0.5, timestamp_tolerance_days=3
        )

        self.assertIsInstance(collision, CollisionResult)
        self.assertTrue(collision.has_collision)
        self.assertTrue(collision.is_stale_collision)
        self.assertEqual(collision.collision_type, "stale")
        self.assertEqual(collision.timestamp_direction, "b_newer")

    @patch('cmd_audit.item_gate.collision.compute_directed_divergence')
    def test_detect_conflict_collision(self, mock_divergence):
        """Test detection of conflict collision (same period)."""
        # Mock high divergence
        mock_divergence.return_value = DirectedDivergence(
            forward_score=3.0, reverse_score=2.8,
            forward_divergence=0.75, reverse_divergence=0.7
        )

        collision = detect_item_collision(
            self.mock_client, self.item_same1, self.item_same2,
            divergence_threshold=0.5, timestamp_tolerance_days=7
        )

        self.assertTrue(collision.has_collision)
        self.assertTrue(collision.is_conflict_collision)
        self.assertEqual(collision.collision_type, "conflict")
        self.assertEqual(collision.timestamp_direction, "same_period")

    @patch('cmd_audit.item_gate.collision.compute_directed_divergence')
    def test_no_collision_low_divergence(self, mock_divergence):
        """Test no collision when divergence is below threshold."""
        # Mock low divergence
        mock_divergence.return_value = DirectedDivergence(
            forward_score=1.0, reverse_score=1.2,
            forward_divergence=0.25, reverse_divergence=0.3
        )

        collision = detect_item_collision(
            self.mock_client, self.item_old, self.item_new,
            divergence_threshold=0.5
        )

        self.assertFalse(collision.has_collision)
        self.assertIsNone(collision.collision_type)

    @patch('cmd_audit.item_gate.collision.detect_item_collision')
    def test_compute_recall_set_divergence(self, mock_detect):
        """Test pairwise collision detection across recall set."""
        recall_set = (self.item_old, self.item_new, self.item_same1)

        # Mock collision detection to return collision for one pair
        mock_collision = CollisionResult(
            item_a=self.item_old, item_b=self.item_new,
            divergence=DirectedDivergence(3.0, 1.0, 0.75, 0.25),
            has_collision=True, collision_type="stale",
            timestamp_direction="b_newer"
        )
        mock_detect.side_effect = [mock_collision, Mock(has_collision=False), Mock(has_collision=False)]

        collisions = compute_recall_set_divergence(self.mock_client, recall_set)

        self.assertEqual(len(collisions), 1)
        self.assertEqual(collisions[0], mock_collision)
        # Should call detect_item_collision C(3,2) = 3 times
        self.assertEqual(mock_detect.call_count, 3)


class TestLOOReconstruction(unittest.TestCase):
    """Tests for Leave-One-Out reconstruction."""

    def setUp(self):
        self.mock_client = Mock()
        self.target_item = MemoryItem(
            memory_id="target",
            text="Paris is the capital of France"
        )
        self.other_items = (
            MemoryItem("item1", "France is a country in Europe"),
            MemoryItem("item2", "European capitals include Berlin and Rome"),
        )
        self.memory_store = self.other_items + (self.target_item,)

    def test_leave_one_out_reconstruct_success(self):
        """Test successful LOO reconstruction."""
        self.mock_client.generate.return_value = "Paris is the main city of France"

        reconstructed = leave_one_out_reconstruct(
            self.mock_client, self.target_item, self.memory_store, "What is the capital of France?"
        )

        self.assertIsNotNone(reconstructed)
        self.assertEqual(reconstructed.text, "Paris is the main city of France")
        self.assertTrue(reconstructed.memory_id.startswith("loo_reconstructed_target"))

    def test_leave_one_out_reconstruct_no_client(self):
        """Test LOO reconstruction with no client."""
        reconstructed = leave_one_out_reconstruct(
            None, self.target_item, self.memory_store, "query"
        )

        self.assertIsNone(reconstructed)

    def test_leave_one_out_reconstruct_empty_store(self):
        """Test LOO reconstruction with empty filtered store."""
        single_item_store = (self.target_item,)

        reconstructed = leave_one_out_reconstruct(
            self.mock_client, self.target_item, single_item_store, "query"
        )

        self.assertIsNone(reconstructed)

    @patch('cmd_audit.item_gate.loo.leave_one_out_reconstruct')
    @patch('cmd_audit.item_gate.loo.compute_directed_divergence')
    def test_compute_loo_divergence_success(self, mock_divergence, mock_reconstruct):
        """Test successful LOO divergence computation."""
        # Mock successful reconstruction
        mock_reconstructed = MemoryItem("recon", "Paris is France's capital city", {})
        mock_reconstruct.return_value = mock_reconstructed

        # Mock divergence indicating item_wrong
        mock_divergence.return_value = DirectedDivergence(
            forward_score=3.5, reverse_score=1.0,
            forward_divergence=0.875, reverse_divergence=0.25
        )

        result = compute_loo_divergence(
            self.mock_client, self.target_item, self.memory_store, "What is the capital?"
        )

        self.assertIsInstance(result, LOOReconstructionResult)
        self.assertTrue(result.reconstruction_successful)
        self.assertTrue(result.has_wrong_classification)
        self.assertEqual(result.item_label, "item_wrong")

    @patch('cmd_audit.item_gate.loo.leave_one_out_reconstruct')
    def test_compute_loo_divergence_reconstruction_failed(self, mock_reconstruct):
        """Test LOO divergence when reconstruction fails."""
        mock_reconstruct.return_value = None

        result = compute_loo_divergence(
            self.mock_client, self.target_item, self.memory_store, "query"
        )

        self.assertFalse(result.reconstruction_successful)
        self.assertIsNone(result.item_label)
        self.assertIsNone(result.divergence)

    def test_order_items_by_experience_moves_likely_item_first(self):
        """LOO priority should use history without dropping any recall item."""
        class FakeStore:
            def score_item_priority(self, query, item):
                return 1.0 if item.memory_id == "target" else 0.0

        ordered = order_items_by_experience(
            "What is the capital?",
            self.memory_store,
            failure_memory_store=FakeStore(),
        )

        self.assertEqual(ordered[0].memory_id, "target")
        self.assertEqual({item.memory_id for item in ordered}, {"target", "item1", "item2"})


class TestItemGate(unittest.TestCase):
    """Tests for main item gate orchestration."""

    def setUp(self):
        self.mock_client = Mock()
        self.target_item = MemoryItem(
            memory_id="target",
            text="Paris is the capital of France",
            store="2024-01-01T10:00:00Z"
        )
        self.recall_set = (
            self.target_item,
            MemoryItem("item1", "France is in Europe", store="2024-01-01T09:00:00Z"),
            MemoryItem("item2", "European cities are diverse", store="2024-01-01T11:00:00Z"),
        )

    @patch('cmd_audit.item_gate.gate.run_item_gate')
    def test_run_item_gate_for_recall_set_stops_on_first_prioritized_hit(
        self, mock_run_item_gate
    ):
        """Recall-set runtime entry should order by experience and stop on hit."""
        class FakeStore:
            def score_item_priority(self, query, item):
                return 1.0 if item.memory_id == "item2" else 0.0

        pass_result = Mock(status=ItemGateStatus.PASS)
        hit_result = Mock(status=ItemGateStatus.ITEM_WRONG)
        mock_run_item_gate.side_effect = [hit_result, pass_result, pass_result]

        result = run_item_gate_for_recall_set(
            self.mock_client,
            self.recall_set,
            "query",
            failure_memory_store=FakeStore(),
        )

        self.assertIs(result, hit_result)
        self.assertEqual(mock_run_item_gate.call_count, 1)
        self.assertEqual(mock_run_item_gate.call_args.args[1].memory_id, "item2")

    @patch('cmd_audit.item_gate.gate.compute_recall_set_divergence')
    @patch('cmd_audit.item_gate.gate.compute_loo_divergence')
    def test_run_item_gate_pass(self, mock_loo, mock_collision):
        """Test item gate with PASS status (no issues found)."""
        # Mock no collisions
        mock_collision.return_value = []

        # Mock LOO with no significant divergence
        mock_loo.return_value = LOOReconstructionResult(
            original_item=self.target_item, reconstructed_item=Mock(),
            divergence=DirectedDivergence(1.0, 1.2, 0.25, 0.3),
            reconstruction_successful=True, item_label=None
        )

        result = run_item_gate(
            self.mock_client, self.target_item, self.recall_set, "What is the capital of France?"
        )

        self.assertIsInstance(result, ItemGateResult)
        self.assertEqual(result.status, ItemGateStatus.PASS)
        self.assertFalse(result.needs_item_treatment)
        self.assertFalse(result.should_skip_tier3)
        self.assertEqual(result.processing_cost, 1)  # One generation for LOO

    @patch('cmd_audit.item_gate.gate.compute_recall_set_divergence')
    def test_run_item_gate_stale_collision(self, mock_collision):
        """Test item gate with stale collision (skips LOO)."""
        # Mock stale collision involving target item
        mock_collision_result = CollisionResult(
            item_a=self.target_item, item_b=self.recall_set[1],
            divergence=DirectedDivergence(3.0, 1.0, 0.75, 0.25),
            has_collision=True, collision_type="stale", timestamp_direction="b_newer"
        )
        mock_collision.return_value = [mock_collision_result]

        result = run_item_gate(
            self.mock_client, self.target_item, self.recall_set, "query"
        )

        self.assertEqual(result.status, ItemGateStatus.ITEM_STALE)
        self.assertTrue(result.needs_item_treatment)
        self.assertTrue(result.should_skip_tier3)
        self.assertTrue(result.can_auto_update)
        self.assertIsNone(result.loo_result)  # LOO skipped due to collision
        self.assertEqual(result.processing_cost, 0)  # No generation, collision detected

    @patch('cmd_audit.item_gate.gate.compute_recall_set_divergence')
    def test_run_item_gate_conflict_collision(self, mock_collision):
        """Test item gate with conflict collision."""
        # Mock conflict collision
        mock_collision_result = CollisionResult(
            item_a=self.target_item, item_b=self.recall_set[1],
            divergence=DirectedDivergence(3.0, 2.8, 0.75, 0.7),
            has_collision=True, collision_type="conflict", timestamp_direction="same_period"
        )
        mock_collision.return_value = [mock_collision_result]

        result = run_item_gate(
            self.mock_client, self.target_item, self.recall_set, "query"
        )

        self.assertEqual(result.status, ItemGateStatus.ITEM_CONFLICT)
        self.assertTrue(result.needs_human_arbitration)
        self.assertFalse(result.can_auto_update)

    @patch('cmd_audit.item_gate.gate.compute_recall_set_divergence')
    @patch('cmd_audit.item_gate.gate.compute_loo_divergence')
    def test_run_item_gate_item_wrong(self, mock_loo, mock_collision):
        """Test item gate detecting item_wrong via LOO."""
        # Mock no collisions
        mock_collision.return_value = []

        # Mock LOO detecting item_wrong
        mock_loo.return_value = LOOReconstructionResult(
            original_item=self.target_item, reconstructed_item=Mock(),
            divergence=DirectedDivergence(3.5, 1.0, 0.875, 0.25),
            reconstruction_successful=True, item_label="item_wrong"
        )

        result = run_item_gate(
            self.mock_client, self.target_item, self.recall_set, "query"
        )

        self.assertEqual(result.status, ItemGateStatus.ITEM_WRONG)
        self.assertTrue(result.should_skip_tier3)

    @patch('cmd_audit.item_gate.gate.compute_recall_set_divergence')
    @patch('cmd_audit.item_gate.gate.compute_loo_divergence')
    def test_run_item_gate_processing_failure(self, mock_loo, mock_collision):
        """Test item gate handling processing failures."""
        # Mock exception in collision detection
        mock_collision.side_effect = Exception("Network error")

        result = run_item_gate(
            self.mock_client, self.target_item, self.recall_set, "query"
        )

        self.assertEqual(result.status, ItemGateStatus.PROCESSING_FAILED)
        self.assertTrue("FAILED" in result.decision_path)

    def test_item_gate_result_properties(self):
        """Test ItemGateResult property methods."""
        result = ItemGateResult(
            target_item=self.target_item, recall_set=self.recall_set, query="test",
            status=ItemGateStatus.ITEM_COMPRESSION_DISTORTED, collision_results=[],
            has_timestamp_conflicts=False, loo_result=None, processing_cost=1,
            decision_path="test"
        )

        self.assertTrue(result.needs_item_treatment)
        self.assertTrue(result.should_skip_tier3)
        self.assertFalse(result.can_auto_update)
        self.assertFalse(result.needs_human_arbitration)


if __name__ == "__main__":
    unittest.main()
