"""Contract tests for the current live-label probe-case builder."""

from __future__ import annotations

import unittest

from cmd_audit.core.models import ProbeCase
from experiments.build_probe_cases import (
    PIPELINE_LABEL_CYCLE,
    _build_case,
    _build_coupled_case,
    _build_multihop_case,
)


SAMPLE_ROW = {
    "query": "Which remembered project code should be used for the follow-up task?",
    "gold_answer": "AlphaBetaGamma",
    "source": "unit",
    "source_item_id": "sample",
    "sub_index": 0,
}


class ProbeCaseBuilderContractTest(unittest.TestCase):
    def test_loo_support_items_do_not_contain_full_gold_answer(self) -> None:
        for label in ("item_wrong", "item_compression_distorted"):
            with self.subTest(label=label):
                case = _build_case("longmemeval", 0, SAMPLE_ROW, label)
                ProbeCase.from_mapping(case)

                support_items = [
                    item
                    for item in case["extracted_memory"]
                    if "support" in item["memory_id"]
                    or "reconstruction" in item["memory_id"]
                ]

                self.assertGreaterEqual(len(support_items), 4)
                for item in support_items:
                    text = item["text"]
                    self.assertNotIn("AlphaBetaGamma", text)
                    self.assertNotIn("The correct remembered answer is", text)
                    self.assertNotIn("Supporting record for reconstruction", text)
                    self.assertNotIn("Uncompressed supporting record", text)

    def test_multihop_cases_load_and_keep_step_metadata(self) -> None:
        for label in PIPELINE_LABEL_CYCLE:
            with self.subTest(label=label):
                case = _build_multihop_case("longmemeval", 0, SAMPLE_ROW, label)
                loaded = ProbeCase.from_mapping(case)

                self.assertEqual(loaded.perturbation_label, label)
                self.assertEqual(case["trajectory_kind"], "multi_hop_single_fault")
                self.assertEqual(len(case["generation_points"]), 2)
                self.assertEqual(case["expected_fault"]["label"], label)

                if label == "graph_error":
                    self.assertTrue(
                        any(item["is_graph_expanded"] for item in case["extracted_memory"])
                    )
                if label == "safety_error":
                    self.assertTrue(case["safety_filter_blocked"])
                    self.assertTrue(
                        any(
                            item["passed_safety_filter"]
                            for item in case["extracted_memory"]
                        )
                    )

    def test_coupled_boundary_case_is_null_labeled_with_pair_metadata(self) -> None:
        case = _build_coupled_case(
            "longmemeval",
            0,
            SAMPLE_ROW,
            ("retrieval_error", "injection_error"),
        )
        loaded = ProbeCase.from_mapping(case)

        self.assertIsNone(loaded.perturbation_label)
        self.assertEqual(case["trajectory_kind"], "coupled_failure_boundary")
        self.assertEqual(case["coupled_labels"], ["retrieval_error", "injection_error"])
        self.assertEqual(case["coupled_failure"]["coalition_recovery"], 1.0)
        self.assertFalse(
            any(point["single_point_recovers"] for point in case["generation_points"])
        )


if __name__ == "__main__":
    unittest.main()
