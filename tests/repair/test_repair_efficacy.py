"""Four-arm repair-efficacy executor contract (Phase 1, E2/E3).

Verifies the load-bearing invariant: every arm's repaired context is a pure
function of (recall_set, action) and never injects gold. Uses a mock client so
it runs without vLLM.
"""

import unittest
from unittest.mock import Mock

from cmd_audit.core.models import (
    BaselineOutput,
    GoldEvidence,
    MemoryItem,
    ProbeCase,
)
from cmd_audit.mcts.actions import apply_pipeline_action, get_legal_actions, PipelineAction
from cmd_audit.repair.efficacy import (
    LABEL_TO_ACTION,
    REPAIR_ARMS,
    run_repair_arm,
    select_label_cmd,
    select_label_random,
)


def _graph_case() -> ProbeCase:
    """A graph_error case: gold in recall, graph-expanded distractor competing."""
    gold = MemoryItem("m_hop2_gold", "Bridge key K resolves to: PARIS", ("e_gold",))
    distractor = MemoryItem(
        "m_hop2_graph_distractor",
        "Bridge key K resolves to: road bike",
        ("e_query",),
        is_graph_expanded=True,
    )
    bridge = MemoryItem("m_hop1_bridge", "first use bridge key K", ("e_bridge",))
    baseline = BaselineOutput(
        baseline_name="vector_memory",
        answer="road bike",
        retrieved_memory_ids=("m_hop1_bridge", "m_hop2_graph_distractor", "m_hop2_gold"),
        answer_score=0.0,
        evidence_score=0.0,
        injected_context=(
            "first use bridge key K\n"
            "Bridge key K resolves to: PARIS\n"
            "Bridge key K resolves to: road bike"
        ),
    )
    return ProbeCase(
        case_id="graph-case-001",
        query="What does bridge key K resolve to?",
        raw_events=(),
        extracted_memory=(bridge, gold, distractor),
        gold_evidence=(GoldEvidence("ev_gold", "Bridge key K resolves to: PARIS", "m_hop2_gold"),),
        gold_answer="PARIS",
        baseline_outputs=(baseline,),
        perturbation_label="graph_error",
    )


class LabelActionMappingTest(unittest.TestCase):
    def test_all_five_step_labels_map_to_actions(self) -> None:
        for label in (
            "retrieval_error", "injection_error", "granularity_error",
            "graph_error", "safety_error",
        ):
            self.assertIn(label, LABEL_TO_ACTION)
            self.assertEqual(LABEL_TO_ACTION[label].value, label)


class GoldFreeConstructionTest(unittest.TestCase):
    """The load-bearing invariant: construction never injects gold answer."""

    def test_graph_action_strips_distractor_without_adding_gold(self) -> None:
        case = _graph_case()
        recall = case.extracted_memory
        base_ctx = case.primary_baseline.injected_context
        # graph_error action: subtractive, removes the graph-expanded distractor.
        repaired = apply_pipeline_action(
            PipelineAction.GRAPH_ERROR, base_ctx, recall, 1,
        )
        # The distractor text must be gone.
        self.assertNotIn("road bike", repaired)
        # Construction added nothing the recall did not already carry: gold count
        # in repaired context must not EXCEED its count in the base context.
        self.assertLessEqual(
            repaired.count("PARIS"), base_ctx.count("PARIS"),
            "graph action must not inject new gold occurrences",
        )

    def test_cmd_selector_maps_label_to_legal_point(self) -> None:
        case = _graph_case()
        choice = select_label_cmd("graph_error", case.extracted_memory, max_depth=2)
        self.assertIsNotNone(choice)
        _, action = choice
        self.assertEqual(action, PipelineAction.GRAPH_ERROR)

    def test_random_selector_is_deterministic_per_case(self) -> None:
        case = _graph_case()
        a = select_label_random(case, case.extracted_memory, max_depth=2)
        b = select_label_random(case, case.extracted_memory, max_depth=2)
        self.assertEqual(a, b)


class FourArmExecutionTest(unittest.TestCase):
    """All four arms run and produce a RepairArmResult under a mock client."""

    def setUp(self) -> None:
        self.case = _graph_case()
        # Mock client: terminal generation returns the gold answer string so
        # rollout scoring has something to score. Construction is what we guard.
        self.client = Mock()
        self.client.generate.return_value = "PARIS"
        # answer_verifier returns "1.0" => recovered.
        self.verifier = Mock(return_value="1.0")

    def _run(self, arm: str, **kw):
        recall = self.case.extracted_memory
        return run_repair_arm(
            self.case, arm,
            client=self.client, answer_verifier=self.verifier,
            base_context=self.case.primary_baseline.injected_context,
            recall_set=recall, max_depth=2,
            intervention_config={"candidate_items": recall, "raw_events": ()},
            **kw,
        )

    def test_all_arms_produce_results(self) -> None:
        no_repair = self._run("no_repair")
        self.assertIsNone(no_repair.selected_action)

        rand = self._run("random")
        self.assertIn(rand.arm, REPAIR_ARMS)

        cmd = self._run("cmd", cmd_label="graph_error")
        self.assertEqual(cmd.selected_action, "graph_error")

        judge = self._run("llm_judge", llm_label_selector=lambda c: "injection_error")
        self.assertEqual(judge.selected_action, "injection_error")

    def test_unknown_arm_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self._run("bogus_arm")


if __name__ == "__main__":
    unittest.main()
