from pathlib import Path
import unittest
import warnings

from cmd_audit import PhraseMatchShortcutWarning, load_probe_cases
from cmd_audit.post_repair import (
    RepairedContext,
    run_post_repair_context_replay,
)


FIXTURE = Path("data/probe_cases/v0_retrieval_error_case.json")


class Decision34PostRepairAgentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.case = load_probe_cases(FIXTURE)[0]
        self.context = RepairedContext(
            case_id=self.case.case_id,
            corrected_memory="Mira chose Lisbon for the Q3 offsite.",
            repair_guidance="Use the corrected memory.",
            repaired_evidence_block="Mira chose Lisbon for the Q3 offsite.",
            original_query=self.case.query,
        )

    def test_agent_is_called_with_query_and_combined_context(self) -> None:
        calls: list[tuple[str, str]] = []

        def agent_generate(query: str, context: str) -> str:
            calls.append((query, context))
            return "Lisbon"

        result = run_post_repair_context_replay(
            self.case,
            self.context,
            agent_generate=agent_generate,
            evidence_scorer=lambda gold, answer: 1.0,
            answer_verifier=lambda answer, gold_answer: "EQUIVALENT",
        )

        self.assertEqual(calls[0][0], self.case.query)
        self.assertIn("Mira chose Lisbon", calls[0][1])
        self.assertEqual(result.repair_assessment, "recovered")

    def test_verifier_equivalent_means_recovered(self) -> None:
        result = run_post_repair_context_replay(
            self.case,
            self.context,
            agent_generate=lambda query, context: "The answer is Lisbon.",
            evidence_scorer=lambda gold, answer: 1.0,
            answer_verifier=lambda answer, gold_answer: "EQUIVALENT",
        )

        self.assertEqual(result.repair_assessment, "recovered")
        self.assertEqual(result.post_repair_answer_score, 1.0)

    def test_non_equivalent_with_evidence_above_threshold_is_partial(self) -> None:
        result = run_post_repair_context_replay(
            self.case,
            self.context,
            agent_generate=lambda query, context: "The answer is not Porto.",
            evidence_scorer=lambda gold, answer: 0.5,
            answer_verifier=lambda answer, gold_answer: "NOT_EQUIVALENT",
        )

        self.assertEqual(result.repair_assessment, "partial")
        self.assertEqual(result.post_repair_answer_score, 0.0)

    def test_evidence_below_threshold_is_failed(self) -> None:
        result = run_post_repair_context_replay(
            self.case,
            self.context,
            agent_generate=lambda query, context: "Unknown",
            evidence_scorer=lambda gold, answer: 0.49,
            answer_verifier=lambda answer, gold_answer: "NOT_EQUIVALENT",
        )

        self.assertEqual(result.repair_assessment, "failed")

    def test_legacy_path_warns(self) -> None:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = run_post_repair_context_replay(self.case, self.context)

        self.assertEqual(result.repair_assessment, "recovered")
        self.assertTrue(
            any("Post-Repair substring fallback active" in str(w.message) for w in caught)
        )
        self.assertTrue(
            any(issubclass(w.category, PhraseMatchShortcutWarning) for w in caught)
        )


if __name__ == "__main__":
    unittest.main()
