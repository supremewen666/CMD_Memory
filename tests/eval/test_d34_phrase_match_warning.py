from pathlib import Path
import unittest
import warnings

from cmd_audit import PhraseMatchShortcutWarning, load_probe_cases
import cmd_audit.replays as replays


FIXTURE = Path("data/probe_cases/v0_retrieval_error_case.json")


class Decision34PhraseMatchWarningTest(unittest.TestCase):
    def setUp(self) -> None:
        self.case = load_probe_cases(FIXTURE)[0]
        replays._scoring_bridge._PHRASE_MATCH_SHORTCUT_WARNED = False

    def test_legacy_shortcut_warning_fires(self) -> None:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = replays.run_oracle_retrieval(self.case)

        self.assertEqual(result.answer, "Lisbon")
        self.assertTrue(
            any("phrase-match shortcut active" in str(w.message) for w in caught)
        )
        self.assertTrue(
            any(issubclass(w.category, PhraseMatchShortcutWarning) for w in caught)
        )

    def test_agent_path_does_not_warn(self) -> None:
        def agent_generate(query: str, context: str) -> str:
            return "Lisbon"

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = replays.run_oracle_retrieval(
                self.case, agent_generate=agent_generate
            )

        self.assertEqual(result.answer, "Lisbon")
        self.assertFalse(
            any("phrase-match shortcut active" in str(w.message) for w in caught)
        )

    def test_scorer_only_path_does_not_fake_gold_answer(self) -> None:
        def scorer(gold_evidence, text: str) -> float:
            self.assertIn("Lisbon", text)
            return 1.0

        result = replays.run_oracle_retrieval(self.case, scorer=scorer)

        self.assertEqual(result.evidence_score, 1.0)
        self.assertEqual(result.answer, "")
        self.assertEqual(result.answer_score, 0.0)
        self.assertEqual(result.recovery_gain, 1.0)


def test_legacy_phrase_match_fixture_enables_warning(legacy_phrase_match_path) -> None:
    case = load_probe_cases(FIXTURE)[0]
    replays._scoring_bridge._PHRASE_MATCH_SHORTCUT_WARNED = False

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        replays.run_oracle_retrieval(case)

    assert any(issubclass(w.category, PhraseMatchShortcutWarning) for w in caught)


if __name__ == "__main__":
    unittest.main()
