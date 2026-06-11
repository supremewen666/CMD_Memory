"""Tests for Tier 3 MCTS implementation."""

import unittest
from unittest.mock import Mock, patch
import time

from cmd_audit.core.models import MemoryItem, GoldEvidence
from cmd_audit.mcts import (
    PipelineAction,
    MCTSNode,
    MCTSTree,
    MCTSConfig,
    MCTSSearch,
    SearchResult,
    NestedValue,
    ValueFunction,
    NaiveWeightedValue,
    get_legal_actions,
    apply_pipeline_action,
    rollout_to_terminal,
    run_mcts_attribution,
)
from cmd_audit.mcts.distill import (
    distill_action_priors,
    flatten_action_priors,
    prior_alignment,
)


class TestPipelineActions(unittest.TestCase):
    """Tests for pipeline actions and interventions."""

    def setUp(self):
        self.recall_set = (
            MemoryItem("item1", "Paris is the capital of France"),
            MemoryItem("item2", "France is in Europe", is_graph_expanded=True),
            MemoryItem(
                "item3",
                "Safety filtered content",
                passed_safety_filter=True,
            ),
        )

    def test_get_legal_actions_base(self):
        """Test basic legal actions without gating."""
        actions = get_legal_actions(self.recall_set, 0, include_gated_actions=False)

        expected = [
            PipelineAction.RETRIEVAL_ERROR,
            PipelineAction.INJECTION_ERROR,
            PipelineAction.GRANULARITY_ERROR,
            PipelineAction.IDENTITY,
        ]

        self.assertEqual(set(actions), set(expected))

    def test_get_legal_actions_with_gating(self):
        """Test legal actions with gated actions enabled."""
        actions = get_legal_actions(self.recall_set, 0, include_gated_actions=True)

        # Should include graph_error (due to is_graph_expanded=True)
        # and safety_error (due to passed_safety_filter metadata)
        self.assertIn(PipelineAction.GRAPH_ERROR, actions)
        self.assertIn(PipelineAction.SAFETY_ERROR, actions)

    def test_get_legal_actions_restricts_to_one_based_hop(self):
        """Experiment 8 can isolate a single hop without pruning identity."""
        hop1_actions = get_legal_actions(
            self.recall_set,
            0,
            include_gated_actions=True,
            restrict_to_hop=2,
        )
        hop2_actions = get_legal_actions(
            self.recall_set,
            1,
            include_gated_actions=True,
            restrict_to_hop=2,
        )

        self.assertEqual(hop1_actions, [PipelineAction.IDENTITY])
        self.assertIn(PipelineAction.RETRIEVAL_ERROR, hop2_actions)

    def test_get_legal_actions_does_not_use_store_string_for_safety(self):
        """Safety gating must use metadata, not a store-name string hack."""
        recall_set = (
            MemoryItem("item1", "Safety word in store", store="safety_passed"),
        )

        actions = get_legal_actions(recall_set, 0, include_gated_actions=True)

        self.assertNotIn(PipelineAction.SAFETY_ERROR, actions)

    def test_apply_pipeline_action_identity(self):
        """Test identity action (no intervention)."""
        context = "Original context"
        result = apply_pipeline_action(
            PipelineAction.IDENTITY, context, self.recall_set, 0
        )
        self.assertEqual(result, context)

    def test_apply_pipeline_action_interventions(self):
        """Test that repair interventions modify context without adding bad evidence."""
        context = "Original context"

        for action in [PipelineAction.RETRIEVAL_ERROR, PipelineAction.INJECTION_ERROR]:
            result = apply_pipeline_action(action, context, self.recall_set, 0)
            self.assertNotEqual(result, context)
            self.assertIn(context, result)  # Original should be preserved
            self.assertNotIn("WRONG_RETRIEVAL", result)
            self.assertNotIn("Malformed", result)

    def test_pipeline_action_properties(self):
        """Test PipelineAction property methods."""
        self.assertTrue(PipelineAction.IDENTITY.is_always_legal)
        self.assertFalse(PipelineAction.IDENTITY.requires_gating)

        self.assertFalse(PipelineAction.GRAPH_ERROR.is_always_legal)
        self.assertTrue(PipelineAction.GRAPH_ERROR.requires_gating)


class TestMCTSTree(unittest.TestCase):
    """Tests for MCTS tree structure and operations."""

    def setUp(self):
        self.root = MCTSNode(
            node_id="root",
            generation_point=0,
            action_sequence=(),
            context="Initial context",
        )
        self.tree = MCTSTree(root=self.root, max_depth=3)

    def test_node_properties(self):
        """Test MCTSNode property methods."""
        self.assertTrue(self.root.is_root)
        self.assertTrue(self.root.is_leaf)
        self.assertEqual(self.root.depth, 0)
        self.assertEqual(self.root.ucb_score, float('inf'))  # Unvisited node

    def test_node_ucb_calculation(self):
        """Test UCB score calculation."""
        # Create parent and child
        child = MCTSNode("child", 1, (PipelineAction.IDENTITY,))
        self.root.add_child(PipelineAction.IDENTITY, child)

        # Update visit counts and Q-values
        self.root.visit_count = 10
        child.visit_count = 5
        child.q_max = 0.8

        # UCB should be > q_max due to exploration term
        self.assertGreater(child.ucb_score, child.q_max)

    def test_tree_expansion(self):
        """Test tree node expansion."""
        legal_actions = [PipelineAction.IDENTITY, PipelineAction.RETRIEVAL_ERROR]

        def mock_context_generator(action, parent_context):
            return f"{parent_context} -> {action.value}"

        # Expand first action
        child1 = self.tree.expand_node(self.root, legal_actions, mock_context_generator)
        self.assertIsNotNone(child1)
        self.assertEqual(len(self.root.children), 1)

        # Expand second action
        child2 = self.tree.expand_node(self.root, legal_actions, mock_context_generator)
        self.assertIsNotNone(child2)
        self.assertEqual(len(self.root.children), 2)

        # No more actions to expand
        child3 = self.tree.expand_node(self.root, legal_actions, mock_context_generator)
        self.assertIsNone(child3)
        self.assertTrue(self.root.is_expanded)

    def test_tree_expansion_prioritizes_identity_baseline(self):
        """Identity baseline must exist before intervention credits are read."""
        legal_actions = [PipelineAction.RETRIEVAL_ERROR, PipelineAction.IDENTITY]

        def mock_context_generator(action, parent_context):
            return f"{parent_context} -> {action.value}"

        child = self.tree.expand_node(self.root, legal_actions, mock_context_generator)

        self.assertIsNotNone(child)
        self.assertEqual(child.action_sequence, (PipelineAction.IDENTITY,))

    def test_tree_expansion_uses_prior_after_identity_baseline(self):
        """Experience prior should order non-identity expansion without pruning."""
        legal_actions = [
            PipelineAction.IDENTITY,
            PipelineAction.RETRIEVAL_ERROR,
            PipelineAction.INJECTION_ERROR,
        ]

        def mock_context_generator(action, parent_context):
            return f"{parent_context} -> {action.value}"

        def action_prior(action):
            return 0.2 if action == PipelineAction.INJECTION_ERROR else 0.0

        first = self.tree.expand_node(
            self.root,
            legal_actions,
            mock_context_generator,
            action_prior=action_prior,
        )
        second = self.tree.expand_node(
            self.root,
            legal_actions,
            mock_context_generator,
            action_prior=action_prior,
        )

        self.assertEqual(first.action_sequence, (PipelineAction.IDENTITY,))
        self.assertEqual(second.action_sequence, (PipelineAction.INJECTION_ERROR,))
        self.assertEqual(second.prior_bonus, 0.2)

    def test_tree_selection(self):
        """Test leaf selection using UCB policy."""
        # Initially should return root
        selected = self.tree.select_leaf()
        self.assertEqual(selected, self.root)

        # Add children with different Q-values
        child1 = MCTSNode("child1", 1, (PipelineAction.IDENTITY,))
        child1.q_max = 0.5
        child1.visit_count = 3

        child2 = MCTSNode("child2", 1, (PipelineAction.RETRIEVAL_ERROR,))
        child2.q_max = 0.8
        child2.visit_count = 2

        self.root.add_child(PipelineAction.IDENTITY, child1)
        self.root.add_child(PipelineAction.RETRIEVAL_ERROR, child2)
        self.root.is_expanded = True
        self.root.visit_count = 5

        # Should select child with higher UCB score
        selected = self.tree.select_leaf()
        self.assertIn(selected, [child1, child2])

    def test_credit_assignment(self):
        """Test action credit computation."""
        # Create tree with identity and intervention
        identity_child = MCTSNode("identity", 1, (PipelineAction.IDENTITY,))
        identity_child.q_max = 0.3

        intervention_child = MCTSNode("intervention", 1, (PipelineAction.RETRIEVAL_ERROR,))
        intervention_child.q_max = 0.7

        self.root.add_child(PipelineAction.IDENTITY, identity_child)
        self.root.add_child(PipelineAction.RETRIEVAL_ERROR, intervention_child)

        credits = self.tree.get_action_credits()

        # Credit should be intervention - identity
        expected_credit = 0.7 - 0.3
        self.assertEqual(credits[0][PipelineAction.RETRIEVAL_ERROR], expected_credit)

    def test_credit_assignment_skips_node_without_identity_baseline(self):
        """Missing identity sibling must not default to Q=0 and inflate credit."""
        intervention_child = MCTSNode("intervention", 1, (PipelineAction.RETRIEVAL_ERROR,))
        intervention_child.q_max = 0.7
        self.root.add_child(PipelineAction.RETRIEVAL_ERROR, intervention_child)

        credits = self.tree.get_action_credits()

        self.assertEqual(credits, {})

    def test_main_culprit_identification(self):
        """Test finding main culprit (highest credit action)."""
        # Setup tree with multiple actions
        actions_and_scores = [
            (PipelineAction.IDENTITY, 0.2),
            (PipelineAction.RETRIEVAL_ERROR, 0.6),
            (PipelineAction.INJECTION_ERROR, 0.8),  # Highest
        ]

        for action, score in actions_and_scores:
            child = MCTSNode(f"child_{action.value}", 1, (action,))
            child.q_max = score
            self.root.add_child(action, child)

        main_culprit = self.tree.find_main_culprit()
        self.assertIsNotNone(main_culprit)

        generation_point, action, credit = main_culprit
        self.assertEqual(generation_point, 0)
        self.assertEqual(action, PipelineAction.INJECTION_ERROR)
        self.assertAlmostEqual(credit, 0.8 - 0.2)  # injection - identity


class TestValueFunction(unittest.TestCase):
    """Tests for nested value function."""

    def setUp(self):
        self.mock_client = Mock()
        self.value_function = ValueFunction(self.mock_client)
        self.gold_evidence = (
            GoldEvidence("ev1", "Paris is the capital"),
            GoldEvidence("ev2", "France is in Europe"),
        )

    @patch('cmd_audit.mcts.value._score_answer_prefix')
    @patch('cmd_audit.mcts.value._continuous_verify')
    def test_evaluate_node_success(self, mock_continuous, mock_answer_prefix):
        """Test successful node evaluation."""
        # Mock evidence scoring (both above threshold)
        mock_continuous.side_effect = [3.0, 2.5]  # evidence1, evidence2
        mock_answer_prefix.return_value = 3.5

        result = self.value_function.evaluate_node(
            "Paris is the capital of France in Europe",
            self.gold_evidence,
            "Paris"
        )

        self.assertIsInstance(result, NestedValue)
        self.assertEqual(result.evidence_count, 2)  # Both above threshold (0.5 * 4 = 2.0)
        self.assertEqual(result.total_evidence, 2)
        self.assertEqual(result.evidence_ceiling, 1.0)  # 2/2
        self.assertAlmostEqual(result.scalar_value, 1.0 * (3.5 / 4.0))

    @patch('cmd_audit.mcts.value._score_answer_prefix')
    @patch('cmd_audit.mcts.value._continuous_verify')
    def test_evaluate_node_partial_evidence(self, mock_continuous, mock_answer_prefix):
        """Test node evaluation with partial evidence coverage."""
        # Mock evidence scoring (one below threshold)
        mock_continuous.side_effect = [3.0, 1.5]  # evidence1 above, evidence2 below
        mock_answer_prefix.return_value = 2.0

        result = self.value_function.evaluate_node(
            "Partial context",
            self.gold_evidence,
            "Answer"
        )

        self.assertEqual(result.evidence_count, 1)  # Only first above threshold
        self.assertEqual(result.evidence_ceiling, 0.5)  # 1/2
        self.assertAlmostEqual(result.scalar_value, 0.5 * (2.0 / 4.0))

    @patch('cmd_audit.mcts.value._continuous_verify')
    def test_evidence_fallback_is_conservative_zero(self, mock_continuous):
        """Unknown evidence entailment must not count toward the ceiling."""
        mock_continuous.return_value = None

        scores = self.value_function._evaluate_evidence_atoms(
            "Partial context",
            self.gold_evidence,
        )

        self.assertEqual(scores, [0.0, 0.0])

    @patch('cmd_audit.mcts.value._continuous_verify')
    @patch('cmd_audit.mcts.value._score_answer_prefix')
    def test_answer_prefix_uses_dedicated_rubric(self, mock_answer_prefix, mock_continuous):
        """rubric_A' must be separate from the evidence FACT/TEXT rubric."""
        mock_continuous.side_effect = [4.0, 4.0]
        mock_answer_prefix.return_value = 1.0

        result = self.value_function.evaluate_node(
            "Evidence is present, but answer is not yet entailed",
            self.gold_evidence,
            "Paris",
        )

        self.assertEqual(mock_continuous.call_count, 2)
        mock_answer_prefix.assert_called_once()
        self.assertEqual(result.answer_score, 1.0)

    def test_evaluate_empty_evidence(self):
        """Test evaluation with no evidence."""
        result = self.value_function.evaluate_node("Context", (), "Answer")

        self.assertEqual(result.evidence_count, 0)
        self.assertEqual(result.total_evidence, 0)
        self.assertEqual(result.evidence_ceiling, 0.0)
        self.assertEqual(result.scalar_value, 0.0)

    def test_nested_value_properties(self):
        """Test NestedValue property methods."""
        value = NestedValue(
            evidence_count=3, total_evidence=4, evidence_ceiling=0.75,
            answer_score=3.0, scalar_value=0.5625,
            per_atom_scores=[3.0, 2.5, 2.8, 1.0], answer_continuous=3.0
        )

        self.assertEqual(value.evidence_fraction, 0.75)
        self.assertEqual(value.answer_normalized, 3.0 / 4.0)
        self.assertFalse(value.is_evidence_complete)
        self.assertTrue(value.has_evidence_gaps)

    @patch('cmd_audit.mcts.value._score_answer_prefix')
    @patch('cmd_audit.mcts.value._continuous_verify')
    def test_naive_weighted_value_removes_evidence_ceiling(
        self, mock_continuous, mock_answer_prefix
    ):
        """Naive ablation lets evidence compensate for a zero answer score."""
        mock_continuous.side_effect = [4.0, 0.0]
        mock_answer_prefix.return_value = 0.0

        result = NaiveWeightedValue(self.mock_client).evaluate_node(
            "partial evidence",
            self.gold_evidence,
            "Paris",
        )

        self.assertEqual(result.evidence_count, 1)
        self.assertAlmostEqual(result.scalar_value, 0.3 * 0.5)


class TestMCTSSearch(unittest.TestCase):
    """Tests for complete MCTS search."""

    def setUp(self):
        self.mock_client = Mock()
        self.recall_set = (
            MemoryItem("item1", "Paris is the capital of France"),
            MemoryItem("item2", "France is in Europe"),
        )
        self.gold_evidence = (
            GoldEvidence("ev1", "Paris is the capital"),
        )
        self.config = MCTSConfig(max_iterations=5, max_depth=2, time_limit_seconds=10.0)

    @patch('cmd_audit.mcts.search.rollout_with_early_stopping')
    @patch('cmd_audit.mcts.value.ValueFunction.evaluate_node')
    def test_mcts_search_basic(self, mock_evaluate, mock_rollout):
        """Test basic MCTS search functionality."""
        # Mock rollout results
        mock_rollout.return_value = Mock(
            rollout_successful=True,
            recovery_gain=0.7,
            terminal_answer="Paris",
            terminal_context="Final context"
        )

        # Mock value function
        mock_evaluate.return_value = NestedValue(
            evidence_count=1, total_evidence=1, evidence_ceiling=1.0,
            answer_score=3.0, scalar_value=0.75,
            per_atom_scores=[3.0], answer_continuous=3.0
        )

        search = MCTSSearch(self.config)
        result = search.search(
            self.mock_client,
            "Initial context",
            self.recall_set,
            self.gold_evidence,
            "Paris"
        )

        self.assertIsInstance(result, SearchResult)
        self.assertGreater(result.iterations_completed, 0)
        self.assertGreater(result.nodes_explored, 1)  # At least root + some children
        self.assertIsNotNone(result.tree)

    @patch('cmd_audit.mcts.search.rollout_with_early_stopping')
    @patch('cmd_audit.mcts.value.ValueFunction.evaluate_node')
    def test_expansion_reruns_generation_point_conditionally(
        self, mock_evaluate, mock_rollout
    ):
        """Expanded nodes should include a generated prefix under interventions."""
        self.mock_client.generate.return_value = "conditioned prefix"
        mock_rollout.return_value = Mock(
            rollout_successful=True,
            recovery_gain=0.0,
            terminal_answer="",
            terminal_context="",
        )
        mock_evaluate.return_value = NestedValue(
            evidence_count=0, total_evidence=1, evidence_ceiling=0.0,
            answer_score=0.0, scalar_value=0.0,
            per_atom_scores=[0.0], answer_continuous=0.0,
        )

        result = MCTSSearch(MCTSConfig(max_iterations=1, max_depth=2)).search(
            self.mock_client,
            "Initial context",
            self.recall_set,
            self.gold_evidence,
            "Paris",
        )

        child = next(iter(result.tree.root.children.values()))
        self.assertIn("Generated prefix 1:", child.context)
        self.assertIn("conditioned prefix", child.context)

    @patch('cmd_audit.mcts.search.rollout_with_early_stopping')
    @patch('cmd_audit.mcts.value.ValueFunction.evaluate_node')
    def test_search_stops_after_shallow_recovery_with_identity_baseline(
        self, mock_evaluate, mock_rollout
    ):
        """Depth-1 recovery should stop after identity sibling establishes credit."""
        from cmd_audit.mcts.rollout import RolloutResult

        mock_evaluate.return_value = NestedValue(
            evidence_count=1, total_evidence=1, evidence_ceiling=1.0,
            answer_score=0.0, scalar_value=0.0,
            per_atom_scores=[4.0], answer_continuous=0.0,
        )
        mock_rollout.side_effect = [
            RolloutResult("ctx", "wrong", 0.0, True, 2),
            RolloutResult("ctx", "Paris", 0.9, True, 0),
        ]

        config = MCTSConfig(
            max_iterations=10,
            max_depth=2,
            early_stopping_threshold=0.8,
            time_limit_seconds=10.0,
        )
        result = MCTSSearch(config).search(
            self.mock_client,
            "Initial context",
            self.recall_set,
            self.gold_evidence,
            "Paris",
        )

        self.assertLess(result.iterations_completed, 10)
        self.assertEqual(mock_rollout.call_count, 2)
        self.assertEqual(result.main_culprit[1], PipelineAction.RETRIEVAL_ERROR)

    @patch('cmd_audit.mcts.search.rollout_with_early_stopping')
    @patch('cmd_audit.mcts.value.ValueFunction.evaluate_node')
    def test_high_credit_full_rollout_does_not_trigger_shallow_stop(
        self, mock_evaluate, mock_rollout
    ):
        """A high terminal credit is not the shallowest-recovery stop condition."""
        from cmd_audit.mcts.rollout import RolloutResult

        mock_evaluate.return_value = NestedValue(
            evidence_count=1, total_evidence=1, evidence_ceiling=1.0,
            answer_score=0.0, scalar_value=0.0,
            per_atom_scores=[4.0], answer_continuous=0.0,
        )
        mock_rollout.side_effect = [
            RolloutResult("ctx", "wrong", 0.0, True, 2),
            RolloutResult("ctx", "Paris", 0.9, True, 2),
            RolloutResult("ctx", "Paris", 0.9, True, 2),
        ]

        config = MCTSConfig(
            max_iterations=3,
            max_depth=2,
            early_stopping_threshold=0.8,
            time_limit_seconds=10.0,
        )
        result = MCTSSearch(config).search(
            self.mock_client,
            "Initial context",
            self.recall_set,
            self.gold_evidence,
            "Paris",
        )

        self.assertEqual(result.iterations_completed, 3)
        self.assertEqual(result.early_stops, 0)

    def test_search_result_properties(self):
        """Test SearchResult property methods."""
        # Create mock search result
        result = SearchResult(
            best_action_sequence=(PipelineAction.RETRIEVAL_ERROR,),
            main_culprit=(0, PipelineAction.RETRIEVAL_ERROR, 0.6),
            action_credits={0: {PipelineAction.RETRIEVAL_ERROR: 0.6, PipelineAction.IDENTITY: 0.0}},
            iterations_completed=10,
            nodes_explored=15,
            terminal_rollouts=8,
            early_stops=0,
            tree=Mock(),
            search_time_seconds=1.5,
            avg_rollout_time=0.2,
        )

        self.assertEqual(result.primary_attribution_label, PipelineAction.RETRIEVAL_ERROR)
        self.assertGreater(result.attribution_confidence, 0.0)

    @patch('cmd_audit.mcts.search.rollout_with_early_stopping')
    def test_run_mcts_attribution_convenience(self, mock_rollout):
        """Test convenience function for MCTS attribution."""
        from cmd_audit.mcts.rollout import RolloutResult
        mock_rollout.return_value = RolloutResult(
            terminal_context="Test context",
            terminal_answer="Test answer",
            recovery_gain=0.5,
            rollout_successful=True,
            generation_points_completed=2,
        )

        result = run_mcts_attribution(
            self.mock_client,
            "Context",
            self.recall_set,
            self.gold_evidence,
            "Answer",
            max_iterations=3,
            max_depth=2
        )

        # Verify the result is a SearchResult
        self.assertIsInstance(result, SearchResult)
        self.assertLessEqual(result.iterations_completed, 3)


class TestRollout(unittest.TestCase):
    """Tests for rollout functionality."""

    def setUp(self):
        self.mock_client = Mock()
        self.recall_set = (
            MemoryItem("item1", "Test memory item"),
        )

    def test_rollout_to_terminal_success(self):
        """Test successful rollout to terminal state."""
        self.mock_client.generate.return_value = "Generated answer"

        result = rollout_to_terminal(
            self.mock_client,
            "Start context",
            1,  # start_generation_point
            3,  # max_generation_points
            self.recall_set,
            "Gold answer"
        )

        self.assertTrue(result.rollout_successful)
        self.assertEqual(result.terminal_answer, "Generated answer")
        self.assertEqual(result.generation_points_completed, 2)  # 3 - 1

    def test_rollout_no_client(self):
        """Test rollout with no client."""
        result = rollout_to_terminal(
            None, "Context", 0, 2, self.recall_set, "Answer"
        )

        self.assertFalse(result.rollout_successful)
        self.assertEqual(result.recovery_gain, 0.0)

    def test_rollout_client_error(self):
        """Test rollout with client generation error."""
        self.mock_client.generate.side_effect = Exception("Generation failed")

        result = rollout_to_terminal(
            self.mock_client, "Context", 0, 2, self.recall_set, "Answer"
        )

        # Rollout completes but with empty answer and zero recovery
        self.assertEqual(result.terminal_answer, "")
        self.assertEqual(result.recovery_gain, 0.0)


class TestDistill(unittest.TestCase):
    """Tests for MCTS credit distillation."""

    def test_distill_action_priors_aligns_positive_credit_with_gold_label(self):
        result = Mock(
            perturbation_label="injection_error",
            mcts_result=Mock(
                action_credits={
                    0: {
                        PipelineAction.IDENTITY: 0.0,
                        PipelineAction.INJECTION_ERROR: 0.8,
                        PipelineAction.RETRIEVAL_ERROR: 0.1,
                    }
                }
            ),
        )

        priors = distill_action_priors([result])
        flat = flatten_action_priors(priors)

        self.assertGreater(
            priors["injection_error"]["injection_error"],
            priors["injection_error"]["retrieval_error"],
        )
        self.assertGreater(flat["injection_error"], 0.5)
        self.assertGreater(prior_alignment(priors), 0.0)

class TestIntegration(unittest.TestCase):
    """Integration tests for complete MCTS pipeline."""

    def setUp(self):
        self.mock_client = Mock()

        # Setup realistic test data
        self.recall_set = (
            MemoryItem("paris_capital", "Paris is the capital of France"),
            MemoryItem("france_europe", "France is located in Europe"),
        )

        self.gold_evidence = (
            GoldEvidence("evidence_1", "Paris is the capital of France"),
        )

        self.initial_context = "Query: What is the capital of France?"

    @patch('cmd_audit.mcts.rollout._compute_recovery_gain')
    @patch('cmd_audit.mcts.value._continuous_verify')
    def test_end_to_end_search(self, mock_continuous, mock_recovery):
        """Test end-to-end MCTS search with mocked components."""
        # Mock client generation
        self.mock_client.generate.return_value = "Paris is the capital."

        # Mock continuous verify (for value function)
        mock_continuous.return_value = 3.0

        # Mock recovery gain computation
        mock_recovery.return_value = 0.8

        # Run search with minimal config
        config = MCTSConfig(max_iterations=3, max_depth=2, time_limit_seconds=5.0)
        search = MCTSSearch(config)

        result = search.search(
            self.mock_client,
            self.initial_context,
            self.recall_set,
            self.gold_evidence,
            "Paris"
        )

        # Verify search completed
        self.assertGreater(result.iterations_completed, 0)
        self.assertIsNotNone(result.tree)
        self.assertGreater(result.nodes_explored, 1)

        # Verify tree structure
        self.assertEqual(result.tree.root.generation_point, 0)
        self.assertEqual(result.tree.root.action_sequence, ())


if __name__ == "__main__":
    unittest.main()
