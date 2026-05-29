import unittest

from scripts.run_post_repair_subset import (
    HEADLINE_8_LABELS,
    REPAIR_4_LABELS,
    load_filtered_cases,
)


class PostRepairSubsetRunnerTest(unittest.TestCase):
    def test_repair_4_labels_exclude_v1_completeness_labels(self) -> None:
        labels = set(REPAIR_4_LABELS)

        self.assertEqual(
            labels,
            {
                "retrieval_error",
                "compression_error",
                "premature_extraction_error",
                "reasoning_error",
            },
        )
        self.assertNotIn("granularity_error", labels)
        self.assertNotIn("graph_error", labels)
        self.assertNotIn("safety_error", labels)

    def test_headline_8_labels_exclude_completeness_only_labels(self) -> None:
        labels = set(HEADLINE_8_LABELS)

        self.assertIn("route_error", labels)
        self.assertIn("ingestion_error", labels)
        self.assertNotIn("granularity_error", labels)
        self.assertNotIn("graph_error", labels)
        self.assertNotIn("safety_error", labels)

    def test_load_filtered_cases_only_returns_requested_labels(self) -> None:
        cases = load_filtered_cases(
            "data/probe_cases",
            labels=set(REPAIR_4_LABELS),
            max_cases=20,
        )

        self.assertTrue(cases)
        self.assertTrue(
            all(case.perturbation_label in REPAIR_4_LABELS for case in cases)
        )


if __name__ == "__main__":
    unittest.main()
