import unittest

from scripts.write_at_scale_retest_run_meta import build_run_meta


class AtScaleRunMetaTest(unittest.TestCase):
    def test_run_meta_records_decision34_required_settings(self) -> None:
        text = build_run_meta(
            agent_model="qwen2.5:7b",
            evaluator_model="eval-model",
            verifier_model="eval-model",
            agent_endpoint="http://localhost:11434/v1",
            evaluator_endpoint="https://eval.example/v1",
            temperature=0.0,
            tie_margin=0.0,
            use_hook=False,
            on_the_fly_baseline_rescore=True,
            random_state=42,
        )

        self.assertIn("use_hook: false", text)
        self.assertIn("on_the_fly_baseline_rescore: true", text)
        self.assertIn("tie_margin: 0.0", text)
        self.assertIn("phrase_match_shortcut_allowed: false", text)
        self.assertIn("label_stripped_replay_context: true", text)


if __name__ == "__main__":
    unittest.main()
