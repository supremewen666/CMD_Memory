"""Contract tests for the current live-label probe-case builder."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from cmd_audit.core.models import ProbeCase
from experiments.build_probe_cases import (
    PIPELINE_LABEL_CYCLE,
    _build_case,
    _build_coupled_case,
    _build_multihop_case,
    build_all,
)


SAMPLE_ROW = {
    "query": "Which remembered project code should be used for the follow-up task?",
    "gold_answer": "AlphaBetaGamma",
    "source": "unit",
    "source_item_id": "sample",
    "sub_index": 0,
}


class ProbeCaseBuilderContractTest(unittest.TestCase):
    def test_only_recurrent_leaves_every_other_output_byte_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_dir = root / "raw"
            output_dir = root / "probe"
            raw_dir.mkdir()
            output_dir.mkdir()

            for source in ("longmemeval", "memoryarena", "toolbench"):
                rows = [
                    {
                        **SAMPLE_ROW,
                        "source": source,
                        "source_item_id": f"{source}-{index}",
                        "query": f"{SAMPLE_ROW['query']} Record {index}.",
                    }
                    for index in range(5)
                ]
                (raw_dir / f"{source}_raw.json").write_text(
                    json.dumps(rows),
                    encoding="utf-8",
                )

            untouched_names = (
                "real_longmemeval_cases.json",
                "real_memoryarena_cases.json",
                "real_toolbench_cases.json",
                "real_three_source_cases.json",
                "real_item_poisoned_hitl_cases.json",
                "real_item_layer_cases.json",
                "real_multihop_cases.json",
                "real_coupled_failure_boundary_cases.json",
                "coupled_failure_inspected_subset.json",
                "probe_case_build_report.md",
            )
            before = {}
            for index, name in enumerate(untouched_names):
                payload = f"sentinel-{index}\n".encode()
                (output_dir / name).write_bytes(payload)
                before[name] = payload
            (output_dir / "real_recurrent_cases.json").write_text(
                "old recurrent bytes\n",
                encoding="utf-8",
            )

            summary = build_all(
                raw_dir=raw_dir,
                output_dir=output_dir,
                target_per_source=1,
                poisoned_per_source=1,
                multihop_per_source=1,
                coupled_per_source=1,
                recurrent_families_per_source=1,
                recurrent_variants_per_family=2,
                item_per_label=0,
                only="recurrent",
            )

            self.assertEqual(summary["total_recurrent_cases"], 6)
            recurrent = json.loads(
                (output_dir / "real_recurrent_cases.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(recurrent), 6)
            for name, payload in before.items():
                self.assertEqual((output_dir / name).read_bytes(), payload, name)

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
