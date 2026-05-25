import json
from pathlib import Path
import tempfile
import unittest

from scripts import build_researcher_agreement as agreement


class ResearcherAgreementArtifactsTest(unittest.TestCase):
    def test_builds_deepseek_and_automation_bias_reports(self) -> None:
        payload = {
            "cases": [
                {
                    "case_id": "c1",
                    "deepseek_label": "retrieval_error",
                    "researcher_label": "retrieval_error",
                    "researcher_blind_label": "retrieval_error",
                },
                {
                    "case_id": "c2",
                    "deepseek_label": "write_error",
                    "researcher_label": "compression_error",
                    "researcher_blind_label": "compression_error",
                },
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            subset = Path(tmpdir) / "researcher_labeled_subset.json"
            subset.write_text(json.dumps(payload), encoding="utf-8")

            researcher_report, automation_report = agreement.build_agreement_artifacts(
                subset,
                out_dir=tmpdir,
                bootstrap_iterations=25,
            )

            self.assertEqual(researcher_report.n, 2)
            self.assertEqual(automation_report.n, 2)
            self.assertTrue((Path(tmpdir) / "researcher_vs_deepseek_kappa.txt").exists())
            self.assertTrue((Path(tmpdir) / "automation_bias_kappa.txt").exists())

    def test_rejects_unpopulated_subset(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            subset = Path(tmpdir) / "researcher_labeled_subset.json"
            subset.write_text(json.dumps({"cases": []}), encoding="utf-8")

            with self.assertRaises(ValueError):
                agreement.build_agreement_artifacts(
                    subset,
                    out_dir=tmpdir,
                    bootstrap_iterations=10,
                )


if __name__ == "__main__":
    unittest.main()
