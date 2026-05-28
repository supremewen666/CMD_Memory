from pathlib import Path
import tempfile
import unittest

from cmd_audit import load_probe_cases, run_case, write_attribution_table
from cmd_audit.core.labels import LabelValidationError, validate_label_base
from cmd_audit.core.models import ProbeCaseError


FIXTURE = Path("data/probe_cases/v0_retrieval_error_case.json")


class RetrievalFailureTracerBulletTest(unittest.TestCase):
    def test_probe_case_contract_loads_retrieval_failure_case(self) -> None:
        case = load_probe_cases(FIXTURE)[0]

        self.assertEqual(case.perturbation_label, "retrieval_error")
        self.assertTrue(case.raw_events)
        self.assertTrue(case.extracted_memory)
        self.assertTrue(case.gold_evidence)
        self.assertEqual(case.gold_answer, "Lisbon")
        self.assertEqual(case.primary_baseline.baseline_name, "vector_memory")
        self.assertEqual(case.primary_baseline.retrieved_memory_ids, ("mem-002",))

    def test_oracle_retrieval_recovers_answer_and_attributes_retrieval_error(
        self,
    ) -> None:
        case = load_probe_cases(FIXTURE)[0]

        result = run_case(case)

        self.assertEqual(result.replay.replay_name, "oracle_retrieval")
        self.assertEqual(result.replay.answer, "Lisbon")
        self.assertEqual(result.replay.answer_score, 1.0)
        self.assertEqual(result.replay.evidence_score, 1.0)
        self.assertEqual(result.attribution.predicted_label, "retrieval_error")
        self.assertTrue(result.attribution_correct)

    def test_attribution_table_contains_first_retrieval_row(self) -> None:
        result = run_case(load_probe_cases(FIXTURE)[0])

        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "attribution_table.csv"
            write_attribution_table([result], output)
            text = output.read_text(encoding="utf-8")

        self.assertIn("case_id,perturbation_label,predicted_label", text)
        self.assertIn("v0-retrieval-001,retrieval_error,retrieval_error", text)


class V0LabelBoundaryTest(unittest.TestCase):
    def test_v0_accepts_only_pipeline_labels(self) -> None:
        self.assertEqual(validate_label_base("retrieval_error"), "retrieval_error")

        with self.assertRaises(LabelValidationError):
            validate_label_base("item_wrong")

        with self.assertRaises(LabelValidationError):
            validate_label_base("route_error")

    def test_probe_case_rejects_gold_evidence_missing_from_extracted_memory(
        self,
    ) -> None:
        raw = load_probe_cases(FIXTURE)[0]
        broken = {
            "case_id": raw.case_id,
            "query": raw.query,
            "raw_events": [
                {"event_id": event.event_id, "text": event.text}
                for event in raw.raw_events
            ],
            "extracted_memory": [
                {
                    "memory_id": item.memory_id,
                    "text": item.text,
                    "source_event_ids": list(item.source_event_ids),
                }
                for item in raw.extracted_memory
            ],
            "gold_evidence": [
                {
                    "evidence_id": "gold-missing",
                    "source_memory_id": "mem-missing",
                    "text": "Missing evidence.",
                }
            ],
            "gold_answer": raw.gold_answer,
            "baseline_outputs": [
                {
                    "baseline_name": raw.primary_baseline.baseline_name,
                    "answer": raw.primary_baseline.answer,
                    "retrieved_memory_ids": list(
                        raw.primary_baseline.retrieved_memory_ids
                    ),
                    "answer_score": raw.primary_baseline.answer_score,
                    "evidence_score": raw.primary_baseline.evidence_score,
                }
            ],
            "perturbation_label": "retrieval_error",
        }

        with self.assertRaises(ProbeCaseError):
            from cmd_audit.core.models import ProbeCase

            ProbeCase.from_mapping(broken)


if __name__ == "__main__":
    unittest.main()
