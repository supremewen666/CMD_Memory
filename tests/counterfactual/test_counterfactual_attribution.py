"""Tests for the retained counterfactual attribution foundation."""

from __future__ import annotations

import unittest

from cmd_audit.core.models import MemoryItem
from cmd_audit.counterfactual import (
    PipelineAction,
    apply_pipeline_action,
    get_legal_actions,
    attribute_single_point,
)
from cmd_audit.counterfactual.context import generate_conditioned_context
from cmd_audit.counterfactual.rollout import rollout_to_terminal


class FakeClient:
    def generate(self, prompt: str) -> str:
        if "NEXT PREFIX:" in prompt:
            return "prefix"
        if "Corrected retrieval candidates" in prompt:
            return "Paris"
        return "Berlin"


def answer_verifier(answer: str, gold_answer: str) -> float:
    return 1.0 if gold_answer in answer else 0.0


class TestPipelineActions(unittest.TestCase):
    def test_get_legal_actions_base_and_restricted_hop(self) -> None:
        recall_set = (MemoryItem("item1", "Paris is in France"),)

        actions = get_legal_actions(recall_set, 0, include_gated_actions=False)
        self.assertEqual(
            actions,
            [
                PipelineAction.RETRIEVAL_ERROR,
                PipelineAction.INJECTION_ERROR,
                PipelineAction.GRANULARITY_ERROR,
                PipelineAction.IDENTITY,
            ],
        )

        self.assertEqual(
            get_legal_actions(recall_set, 0, restrict_to_hop=2),
            [PipelineAction.IDENTITY],
        )
        self.assertIn(
            PipelineAction.RETRIEVAL_ERROR,
            get_legal_actions(recall_set, 1, restrict_to_hop=2),
        )

    def test_apply_pipeline_action_retrieval_adds_missed_candidate(self) -> None:
        recall_set = (
            MemoryItem("recall", "France is in Europe", source_event_ids=("e1",)),
        )
        missed = MemoryItem(
            "gold",
            "Paris is the capital of France",
            source_event_ids=("e2",),
        )

        result = apply_pipeline_action(
            PipelineAction.RETRIEVAL_ERROR,
            "Context",
            recall_set,
            0,
            intervention_config={"candidate_items": recall_set + (missed,)},
        )

        self.assertIn("Corrected retrieval candidates", result)
        self.assertIn("Paris is the capital", result)

    def test_item_signal_hints_order_and_downweight_evidence(self) -> None:
        recall_set = (
            MemoryItem("old", "Kai chose Berlin for the workshop"),
            MemoryItem("new", "Kai chose Madrid for the workshop"),
        )

        result = apply_pipeline_action(
            PipelineAction.INJECTION_ERROR,
            "Context",
            recall_set,
            0,
            intervention_config={"item_signal_hints": {"old": -1.0, "new": 1.0}},
        )

        self.assertLess(result.index("[new priority]"), result.index("[old downweighted]"))


class TestCounterfactualAttribution(unittest.TestCase):
    def test_generate_conditioned_context_appends_prefix(self) -> None:
        result = generate_conditioned_context(FakeClient(), "Context", 1)
        self.assertIn("Generated prefix 1", result)

    def test_rollout_to_terminal_scores_answer(self) -> None:
        result = rollout_to_terminal(
            FakeClient(),
            "Corrected retrieval candidates:\n- Paris",
            1,
            1,
            (),
            "Paris",
            answer_verifier=answer_verifier,
        )

        self.assertTrue(result.rollout_successful)
        self.assertEqual(result.recovery_gain, 1.0)

    def test_attribute_single_point_returns_single_point_search_result(self) -> None:
        recall_set = (
            MemoryItem("recall", "France is in Europe", source_event_ids=("e1",)),
        )
        missed = MemoryItem(
            "gold",
            "Paris is the capital of France",
            source_event_ids=("e2",),
        )

        result = attribute_single_point(
            FakeClient(),
            "Query: capital of France",
            recall_set,
            (),
            "Paris",
            max_depth=1,
            answer_verifier=answer_verifier,
            intervention_config={"candidate_items": recall_set + (missed,)},
        )

        self.assertEqual(result.primary_attribution_label, PipelineAction.RETRIEVAL_ERROR)
        self.assertEqual(result.main_culprit, (0, PipelineAction.RETRIEVAL_ERROR, 1.0))
        self.assertIn(PipelineAction.IDENTITY, result.action_credits[0])
        self.assertGreater(result.terminal_rollouts, 0)


if __name__ == "__main__":
    unittest.main()
