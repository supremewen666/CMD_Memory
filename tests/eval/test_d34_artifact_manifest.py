from pathlib import Path
import tempfile
import unittest

from scripts.write_llm_artifact_manifest import build_manifest


class ArtifactManifestTest(unittest.TestCase):
    def test_manifest_records_llm_stack_and_semantic_shifts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "at_scale_llm_retest.csv").write_text("case_id\n", encoding="utf-8")
            (root / "sandbox").mkdir()
            (root / "sandbox" / "repair_label_summary.csv").write_text(
                "label\n",
                encoding="utf-8",
            )

            text = build_manifest(
                root,
                dataset_version="v1.0",
                dataset_hash="abc123",
                git_sha="deadbeef",
                evaluator_model="eval-model",
            )

        self.assertIn("on_the_fly_baseline_rescore: true", text)
        self.assertIn("tie_margin: 0.0", text)
        self.assertIn("label-stripped", text)
        self.assertIn("sandbox/recurrence_comparison.csv is dropped", text)
        self.assertIn("at_scale_llm_retest.csv", text)


if __name__ == "__main__":
    unittest.main()
