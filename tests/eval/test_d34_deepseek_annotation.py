from pathlib import Path
import tempfile
import unittest

from scripts import annotate_perturbation_labels as annotate


class DeepseekAnnotationScriptTest(unittest.TestCase):
    def test_parse_plain_label_response(self) -> None:
        self.assertEqual(
            annotate.parse_label_response("retrieval_error"),
            "retrieval_error",
        )

    def test_parse_json_label_response(self) -> None:
        self.assertEqual(
            annotate.parse_label_response('{"perturbation_label": "reasoning_error"}'),
            "reasoning_error",
        )

    def test_build_user_prompt_handles_probe_case_shape(self) -> None:
        row = {
            "case_id": "c1",
            "query": "Where is the key?",
            "gold_answer": "The key is in Madrid.",
            "gold_evidence": [{"text": "The key is in Madrid."}],
            "extracted_memory": [
                {
                    "memory_id": "m1",
                    "store": "vector",
                    "granularity": "event",
                    "text": "The key is in Madrid.",
                }
            ],
            "primary_baseline": {
                "retrieved_memory_ids": ["m1"],
                "injected_context": "The key is in Madrid.",
                "answer": "Barcelona",
            },
            "has_ingestion_trace": True,
        }

        prompt = annotate.build_user_prompt(row)

        self.assertIn("Case ID: c1", prompt)
        self.assertIn("Baseline retrieval IDs: m1", prompt)
        self.assertIn("Has ingestion trace: True", prompt)

    def test_build_user_prompt_handles_real_case_baseline_list_shape(self) -> None:
        row = {
            "case_id": "c1",
            "query": "Where is the key?",
            "gold_answer": "The key is in Madrid.",
            "gold_evidence": [{"text": "The key is in Madrid."}],
            "extracted_memory": [{"memory_id": "m1", "text": "distractor"}],
            "baseline_outputs": [
                {
                    "baseline_name": "vector_memory",
                    "retrieved_memory_ids": ["m1"],
                    "injected_context": "distractor",
                    "answer": "Barcelona",
                }
            ],
            "has_ingestion_trace": True,
        }

        prompt = annotate.build_user_prompt(row)

        self.assertIn("Baseline retrieval IDs: m1", prompt)
        self.assertIn("Baseline injected context: distractor", prompt)
        self.assertIn("Baseline answer: Barcelona", prompt)

    def test_build_user_prompt_handles_cleaned_case_shape(self) -> None:
        row = {
            "case_id": "clean-1",
            "source": "longmemeval/single",
            "query": "Which tool implements 6S?",
            "gold_answer": "SIAC_GEE implements 6S.",
            "answer_session_ids": ["answer-1"],
            "haystack_session_count": 2,
            "haystack_sessions": [
                [
                    {"role": "user", "content": "Tell me about SIAC_GEE."},
                    {
                        "role": "assistant",
                        "content": "SIAC_GEE implements the 6S algorithm.",
                    },
                ]
            ],
        }

        prompt = annotate.build_user_prompt(row)

        self.assertIn("source=longmemeval/single", prompt)
        self.assertIn("answer_session_ids", prompt)
        self.assertIn("SIAC_GEE implements the 6S algorithm", prompt)
        self.assertIn("Baseline retrieval IDs: <not available>", prompt)

    def test_compare_annotations_reports_agreement_and_mismatches(self) -> None:
        comparison = annotate.compare_annotations(
            [
                {"case_id": "c1", "perturbation_label": "retrieval_error"},
                {"case_id": "c2", "perturbation_label": "write_error"},
            ],
            {"c1": "retrieval_error", "c2": "compression_error"},
        )

        self.assertEqual(comparison["compared"], 2)
        self.assertEqual(comparison["matched"], 1)
        self.assertEqual(comparison["agreement"], 0.5)
        self.assertEqual(len(comparison["mismatches"]), 1)

    def test_write_reproducibility_report_records_threshold_and_honesty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "report.txt"
            annotate.write_reproducibility_report(
                path,
                {
                    "compared": 2,
                    "matched": 1,
                    "agreement": 0.5,
                    "missing_case_ids": [],
                    "mismatches": [
                        {
                            "case_id": "c2",
                            "expected": "write_error",
                            "observed": "retrieval_error",
                        }
                    ],
                },
                model="deepseek-v4-pro-max",
                base_url="https://api.deepseek.com/v1",
                temperature=0.0,
                top_p="provider-default",
                elapsed_seconds=1.25,
                total_calls=2,
                run_status="compare_only",
                notes="unit-test note",
            )
            text = path.read_text(encoding="utf-8")

        self.assertIn("run_status: compare_only", text)
        self.assertIn("threshold_status: fail", text)
        self.assertIn("Original labeling script was not preserved", text)
        self.assertIn("unit-test note", text)

    def test_main_compare_only_writes_report_without_api_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            input_path = root / "input.json"
            annotations_path = root / "annotations.json"
            existing_path = root / "existing.json"
            report_path = root / "report.txt"
            input_path.write_text("[]\n", encoding="utf-8")
            annotations_path.write_text(
                '[{"case_id": "c1", "perturbation_label": "retrieval_error"}]\n',
                encoding="utf-8",
            )
            existing_path.write_text(
                '[{"case_id": "c1", "perturbation_label": "retrieval_error"}]\n',
                encoding="utf-8",
            )

            exit_code = annotate.main(
                [
                    "--input",
                    str(input_path),
                    "--annotations",
                    str(annotations_path),
                    "--existing-labels",
                    str(existing_path),
                    "--report",
                    str(report_path),
                    "--compare-only",
                ]
            )

            text = report_path.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertIn("run_status: compare_only", text)
        self.assertIn("agreement: 1.000000", text)


if __name__ == "__main__":
    unittest.main()
