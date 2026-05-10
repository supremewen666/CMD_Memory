from pathlib import Path
import tempfile
import unittest

from cmd_audit import load_probe_cases, run_case, run_cases, write_attribution_table
from cmd_audit.harness import write_confusion_matrix_table
from cmd_audit.labels import V0_PIPELINE_LABEL_ORDER


PREMATURE_FIXTURE = Path("data/probe_cases/v0_premature_extraction_error_case.json")
ISSUE3_SUITE = Path("data/probe_cases/v0_issue3_cases.json")


class VerbatimEventOracleBoundaryTest(unittest.TestCase):
    def test_raw_event_only_evidence_is_valid_probe_case(self) -> None:
        case = load_probe_cases(PREMATURE_FIXTURE)[0]

        self.assertEqual(case.perturbation_label, "premature_extraction_error")
        self.assertEqual(case.gold_evidence[0].source_event_id, "evt-101")
        self.assertIsNone(case.gold_evidence[0].source_memory_id)

    def test_verbatim_event_oracle_beats_oracle_retrieval_for_extraction_loss(self) -> None:
        result = run_case(load_probe_cases(PREMATURE_FIXTURE)[0])
        replays = {replay.replay_name: replay for replay in result.replays}

        self.assertEqual(replays["oracle_retrieval"].answer_score, 0.0)
        self.assertEqual(replays["oracle_retrieval"].evidence_score, 0.0)
        self.assertEqual(replays["verbatim_event_oracle"].answer, "Berlin")
        self.assertEqual(replays["verbatim_event_oracle"].answer_score, 1.0)
        self.assertEqual(replays["verbatim_event_oracle"].evidence_score, 1.0)
        self.assertEqual(result.attribution.predicted_label, "premature_extraction_error")
        self.assertEqual(result.attribution.top_replay, "verbatim_event_oracle")
        self.assertTrue(result.attribution_correct)

    def test_issue3_table_contains_per_replay_gain_columns(self) -> None:
        results = run_cases(load_probe_cases(ISSUE3_SUITE))

        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "attribution_table.csv"
            write_attribution_table(results, output)
            text = output.read_text(encoding="utf-8")

        for replay_name in (
            "oracle_write",
            "oracle_compression",
            "verbatim_event_oracle",
            "oracle_retrieval",
            "injection_oracle",
            "evidence_given_reasoning",
        ):
            self.assertIn(f"{replay_name}_recovery_gain", text)
        self.assertIn(
            "v0-premature-extraction-001,premature_extraction_error,"
            "premature_extraction_error,verbatim_event_oracle",
            text,
        )

    def test_issue3_suite_attributes_all_v0_pipeline_labels(self) -> None:
        expected_top_replays = {
            "write_error": "oracle_write",
            "compression_error": "oracle_compression",
            "premature_extraction_error": "verbatim_event_oracle",
            "retrieval_error": "oracle_retrieval",
            "injection_error": "injection_oracle",
            "reasoning_error": "evidence_given_reasoning",
        }

        results = run_cases(load_probe_cases(ISSUE3_SUITE))
        by_label = {result.perturbation_label: result for result in results}

        self.assertEqual(set(by_label), set(V0_PIPELINE_LABEL_ORDER))
        for label, result in by_label.items():
            self.assertEqual(result.attribution.predicted_label, label)
            self.assertEqual(result.attribution.top_replay, expected_top_replays[label])
            self.assertEqual(len(result.replays), len(V0_PIPELINE_LABEL_ORDER))
            self.assertTrue(result.attribution_correct)

    def test_confusion_matrix_contains_one_diagonal_count_per_v0_label(self) -> None:
        results = run_cases(load_probe_cases(ISSUE3_SUITE))

        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "attribution_confusion_matrix.csv"
            write_confusion_matrix_table(results, output)
            lines = output.read_text(encoding="utf-8").splitlines()

        self.assertEqual(
            lines[0],
            "gold_label,write_error,compression_error,premature_extraction_error,"
            "retrieval_error,injection_error,reasoning_error",
        )
        for label in V0_PIPELINE_LABEL_ORDER:
            row = next(line for line in lines if line.startswith(f"{label},"))
            values = row.split(",")[1:]
            self.assertEqual(values[list(V0_PIPELINE_LABEL_ORDER).index(label)], "1")


if __name__ == "__main__":
    unittest.main()
