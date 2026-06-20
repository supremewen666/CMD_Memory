"""Gold-free repair execution core contract (Phase 1, E2/E3).

Verifies the load-bearing invariant: a repair's context is a pure function of
(recall_set, action) and never reads ``case.gold_*`` during construction. Uses
a mock client so it runs without vLLM. Multi-arm comparison scaffolding lives in
``experiments/run_experiment_14_repair_efficacy.py``.
"""

import unittest
from unittest.mock import Mock

from cmd_audit.core.models import (
    BaselineOutput,
    GoldEvidence,
    MemoryItem,
    ProbeCase,
)
from cmd_audit.counterfactual.actions import PipelineAction, apply_pipeline_action
from cmd_audit.repair.efficacy import (
    LABEL_TO_ACTION,
    run_single_repair,
    select_label_cmd,
)


def _retrieval_case() -> ProbeCase:
    """A retrieval_error case: correct memory exists but was not retrieved."""
    bridge = MemoryItem("m_hop1_bridge", "first use bridge key K", ("e_bridge",))
    gold = MemoryItem("m_hop2_gold", "Bridge key K resolves to: PARIS", ("e_gold",))
    baseline = BaselineOutput(
        baseline_name="vector_memory",
        answer="unknown",
        retrieved_memory_ids=("m_hop1_bridge",),
        answer_score=0.0,
        evidence_score=0.0,
        injected_context="first use bridge key K",
    )
    return ProbeCase(
        case_id="retrieval-case-001",
        query="What does bridge key K resolve to?",
        raw_events=(),
        extracted_memory=(bridge, gold),
        gold_evidence=(
            GoldEvidence("ev_gold", "Bridge key K resolves to: PARIS", "m_hop2_gold"),
        ),
        gold_answer="PARIS",
        baseline_outputs=(baseline,),
        perturbation_label="retrieval_error",
    )


class LabelActionMappingTest(unittest.TestCase):
    def test_live_step_labels_map_to_actions(self) -> None:
        for label in (
            "retrieval_error",
            "injection_error",
            "granularity_error",
            "safety_error",
        ):
            self.assertIn(label, LABEL_TO_ACTION)
            self.assertEqual(LABEL_TO_ACTION[label].value, label)

    def test_graph_error_is_not_a_live_action(self) -> None:
        self.assertNotIn("graph_error", LABEL_TO_ACTION)


class SelectLabelCmdTest(unittest.TestCase):
    def test_maps_label_to_legal_point(self) -> None:
        case = _retrieval_case()
        recall = (case.extracted_memory[0],)
        choice = select_label_cmd("retrieval_error", recall, max_depth=2)
        self.assertIsNotNone(choice)
        _, action = choice
        self.assertEqual(action, PipelineAction.RETRIEVAL_ERROR)

    def test_honors_supplied_gen_point(self) -> None:
        case = _retrieval_case()
        recall = (case.extracted_memory[0],)
        choice = select_label_cmd(
            "retrieval_error", recall, max_depth=2, gen_point=1
        )
        self.assertIsNotNone(choice)
        gen_point, action = choice
        self.assertEqual(gen_point, 1)
        self.assertEqual(action, PipelineAction.RETRIEVAL_ERROR)

    def test_unknown_label_returns_none(self) -> None:
        case = _retrieval_case()
        self.assertIsNone(
            select_label_cmd("not_a_label", case.extracted_memory, max_depth=2)
        )


class GoldFreeConstructionTest(unittest.TestCase):
    def test_retrieval_action_uses_candidates_not_gold_fields(self) -> None:
        case = _retrieval_case()
        recall = (case.extracted_memory[0],)
        base_ctx = case.primary_baseline.injected_context

        repaired = apply_pipeline_action(
            PipelineAction.RETRIEVAL_ERROR,
            base_ctx,
            recall,
            1,
            intervention_config={"candidate_items": case.extracted_memory},
        )

        self.assertIn("Corrected retrieval candidates", repaired)
        self.assertIn("PARIS", repaired)


class RunSingleRepairTest(unittest.TestCase):
    """run_single_repair executes a choice (or None) and scores absolute gain."""

    def setUp(self) -> None:
        self.case = _retrieval_case()
        self.recall = (self.case.extracted_memory[0],)
        self.client = Mock()
        self.client.generate.return_value = "PARIS"
        self.verifier = Mock(return_value="1.0")

    def _run(self, choice):
        return run_single_repair(
            self.case,
            choice,
            client=self.client,
            answer_verifier=self.verifier,
            base_context=self.case.primary_baseline.injected_context,
            recall_set=self.recall,
            max_depth=2,
            intervention_config={
                "candidate_items": self.case.extracted_memory,
                "raw_events": (),
            },
        )

    def test_none_choice_is_identity_no_action(self) -> None:
        res = self._run(None)
        self.assertIsNone(res.selected_action)
        self.assertIsNone(res.generation_point)

    def test_choice_records_action_and_point(self) -> None:
        choice = select_label_cmd("retrieval_error", self.recall, max_depth=2)
        res = self._run(choice)
        self.assertEqual(res.selected_action, "retrieval_error")
        self.assertEqual(res.generation_point, choice[0])


if __name__ == "__main__":
    unittest.main()
