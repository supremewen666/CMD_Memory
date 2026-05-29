import json
from pathlib import Path
import tempfile
import unittest

from scripts.sample_surrogate_gap_holdout import (
    CaseRef,
    load_case_refs,
    load_excluded_case_ids,
    sample_holdout,
    write_holdout,
)


class SurrogateGapHoldoutSamplingTest(unittest.TestCase):
    def test_samples_gold_dependent_labels_and_excludes_researcher_subset(self) -> None:
        refs = [
            CaseRef("w1", "write_error", "fixture"),
            CaseRef("w2", "write_error", "fixture"),
            CaseRef("c1", "compression_error", "fixture"),
            CaseRef("c2", "compression_error", "fixture"),
            CaseRef("p1", "premature_extraction_error", "fixture"),
            CaseRef("p2", "premature_extraction_error", "fixture"),
            CaseRef("i1", "injection_error", "fixture"),
            CaseRef("i2", "injection_error", "fixture"),
            CaseRef("r1", "retrieval_error", "fixture"),
        ]

        sampled = sample_holdout(
            refs,
            excluded_case_ids={"w1"},
            per_label=1,
            random_state=43,
        )

        self.assertEqual(len(sampled), 4)
        self.assertNotIn("w1", {ref.case_id for ref in sampled})
        self.assertEqual(
            {ref.perturbation_label for ref in sampled},
            {
                "write_error",
                "compression_error",
                "premature_extraction_error",
                "injection_error",
            },
        )

    def test_json_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cases_path = Path(tmpdir) / "cases.json"
            subset_path = Path(tmpdir) / "subset.json"
            out_path = Path(tmpdir) / "holdout.json"
            cases_path.write_text(
                json.dumps(
                    {
                        "cases": [
                            {"case_id": "w1", "perturbation_label": "write_error"},
                            {"case_id": "c1", "perturbation_label": "compression_error"},
                            {
                                "case_id": "p1",
                                "perturbation_label": "premature_extraction_error",
                            },
                            {"case_id": "i1", "perturbation_label": "injection_error"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            subset_path.write_text(json.dumps({"cases": []}), encoding="utf-8")

            refs = load_case_refs([cases_path])
            excluded = load_excluded_case_ids(subset_path)
            sampled = sample_holdout(refs, excluded_case_ids=excluded, per_label=1)
            write_holdout(
                out_path,
                sampled,
                source_cases=[str(cases_path)],
                excluded_subset=str(subset_path),
                random_state=43,
                per_label=1,
            )

            payload = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["cases"]), 4)
            self.assertEqual(payload["counts_by_label"]["write_error"], 1)


if __name__ == "__main__":
    unittest.main()
