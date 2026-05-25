from pathlib import Path
import tempfile
import unittest

from cmd_audit import load_probe_cases, run_case_full_v1, write_attribution_table


FIXTURE = Path("data/probe_cases/v0_retrieval_error_case.json")


class Decision34BaselineRescoreTest(unittest.TestCase):
    def test_on_the_fly_baseline_rescore_changes_recovery_gain_denominator(self) -> None:
        case = load_probe_cases(FIXTURE)[0]

        def agent_generate(query: str, context: str) -> str:
            if "COUNTERFACTUAL EVIDENCE BLOCK" in context and "Lisbon" in context:
                return "Lisbon"
            if "Mira chose Lisbon" in context:
                return "Lisbon"
            return "baseline answer"

        def evidence_scorer(gold_evidence, text: str) -> float:
            if text == "baseline answer":
                return 0.25
            if "Lisbon" in text:
                return 1.0
            return 0.0

        result = run_case_full_v1(
            case,
            agent_generate=agent_generate,
            scorer=evidence_scorer,
            evidence_scorer=evidence_scorer,
            answer_verifier=lambda answer, gold_answer: "EQUIVALENT",
            on_the_fly_baseline_rescore=True,
        )

        self.assertEqual(result.audit.baseline_evidence_score_llm, 0.25)
        self.assertEqual(result.audit.replay.replay_name, "oracle_retrieval")
        self.assertEqual(result.audit.replay.recovery_gain, 0.75)

    def test_attribution_table_writes_optional_llm_baseline_column(self) -> None:
        case = load_probe_cases(FIXTURE)[0]
        result = run_case_full_v1(
            case,
            agent_generate=lambda query, context: "Lisbon",
            scorer=lambda gold, text: 1.0,
            evidence_scorer=lambda gold, text: 1.0,
            answer_verifier=lambda answer, gold_answer: "EQUIVALENT",
            on_the_fly_baseline_rescore=True,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "attribution.csv"
            write_attribution_table([result.audit], path)
            text = path.read_text(encoding="utf-8")

        self.assertIn("baseline_evidence_score_llm", text.splitlines()[0])
        self.assertIn(",1.000,", text)


if __name__ == "__main__":
    unittest.main()
