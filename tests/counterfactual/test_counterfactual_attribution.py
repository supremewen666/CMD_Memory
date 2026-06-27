"""Tests for the retained counterfactual attribution foundation."""

from __future__ import annotations

import unittest

from cmd_audit.core.models import MemoryItem
from cmd_audit.counterfactual import (
    OperatorSpec,
    PipelineAction,
    SelectPredicate,
    TransformPrimitive,
    apply_operator_static,
    apply_pipeline_action,
    evaluate_operator_spec,
    get_legal_actions,
    operator_dsl_for_action,
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

    def test_operator_spec_filters_identity_and_merges_hints(self) -> None:
        spec = OperatorSpec.from_actions(
            (
                (1, PipelineAction.IDENTITY),
                (0, PipelineAction.RETRIEVAL_ERROR),
                (2, PipelineAction.INJECTION_ERROR),
            ),
            item_signal_hints={"new": 1.0},
        )

        self.assertEqual(
            spec.action_by_generation_point(),
            {
                0: PipelineAction.RETRIEVAL_ERROR,
                2: PipelineAction.INJECTION_ERROR,
            },
        )
        self.assertEqual(
            spec.format(),
            "gp0:retrieval_error+gp2:injection_error+hints[new=1]",
        )

        merged = spec.intervention_config(
            {"item_signal_hints": {"old": -1.0, "invalid": object()}}
        )

        self.assertEqual(merged["item_signal_hints"], {"old": -1.0, "new": 1.0})
        self.assertEqual(spec.steps[0].selector, "missed_candidates")
        self.assertEqual(spec.steps[0].transform, "add_from_store")
        self.assertEqual(
            spec.to_dict()["steps"][0],
            {
                "generation_point": 0,
                "hop_index": 1,
                "action": "retrieval_error",
                "select": "missed_candidates",
                "transform": "add_from_store",
            },
        )

        with self.assertRaises(ValueError):
            OperatorSpec.from_actions(
                (
                    (0, PipelineAction.RETRIEVAL_ERROR),
                    (0, PipelineAction.INJECTION_ERROR),
                )
            )

    def test_apply_operator_static_runs_composite_with_parameters(self) -> None:
        old = MemoryItem("old", "Kai chose Berlin for the workshop", source_event_ids=("e1",))
        new = MemoryItem("new", "Kai chose Madrid for the workshop", source_event_ids=("e2",))
        missed = MemoryItem("missed", "Kai booked the venue in Madrid", source_event_ids=("e3",))
        recall_set = (old, new)
        spec = OperatorSpec.from_actions(
            (
                (0, PipelineAction.RETRIEVAL_ERROR),
                (1, PipelineAction.INJECTION_ERROR),
            ),
            item_signal_hints={"old": -1.0, "new": 1.0},
        )

        result = apply_operator_static(
            "Context",
            recall_set,
            spec,
            intervention_config={"candidate_items": recall_set + (missed,)},
        )

        self.assertIn("Corrected retrieval candidates", result)
        self.assertIn("Kai booked the venue in Madrid", result)
        self.assertIn("Normalized injected memory", result)
        self.assertLess(result.index("[new priority]"), result.index("[old downweighted]"))

    def test_action_dsl_registry_exposes_select_transform(self) -> None:
        dsl = operator_dsl_for_action(PipelineAction.GRANULARITY_ERROR)

        self.assertIsNotNone(dsl)
        self.assertEqual(dsl.selector, SelectPredicate.COARSE_RECALL)
        self.assertEqual(dsl.transform, TransformPrimitive.EXPAND_GRANULARITY)

    def test_evaluate_operator_spec_scores_terminal_answer(self) -> None:
        recall_set = (
            MemoryItem("recall", "France is in Europe", source_event_ids=("e1",)),
        )
        missed = MemoryItem(
            "gold",
            "Paris is the capital of France",
            source_event_ids=("e2",),
        )
        spec = OperatorSpec.single(0, PipelineAction.RETRIEVAL_ERROR)

        result = evaluate_operator_spec(
            FakeClient(),
            "Query: capital of France",
            recall_set,
            spec,
            max_depth=1,
            gold_answer="Paris",
            answer_verifier=answer_verifier,
            intervention_config={"candidate_items": recall_set + (missed,)},
        )

        self.assertTrue(result.successful)
        self.assertEqual(result.score, 1.0)


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
