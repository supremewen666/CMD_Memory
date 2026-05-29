import unittest

from experiments.context_construction import (
    EXPERIMENT_01_CONTEXT_MODES,
    _render_contexts,
    _token_count,
    compute_mcnemar,
)


class Decision34Experiment1ModesTest(unittest.TestCase):
    def test_context_modes_include_corrected_only_padded(self) -> None:
        contexts = _render_contexts(
            query="Where is the offsite?",
            wrong_memory="Mira chose Porto.",
            cause="retrieval selected stale memory.",
            corrected_memory="Mira chose Lisbon.",
            repair_guidance="Use the corrected city.",
        )

        self.assertEqual(set(contexts), set(EXPERIMENT_01_CONTEXT_MODES))
        self.assertIn("corrected_only_padded", contexts)
        self.assertIn("Neutral Padding", contexts["corrected_only_padded"])

    def test_corrected_only_padded_matches_contrastive_token_count(self) -> None:
        contexts = _render_contexts(
            query="Where is the offsite?",
            wrong_memory="Mira chose Porto.",
            cause="retrieval selected stale memory.",
            corrected_memory="Mira chose Lisbon.",
            repair_guidance="Use the corrected city.",
        )

        delta = abs(
            _token_count(contexts["corrected_only_padded"])
            - _token_count(contexts["contrastive"])
        )
        self.assertLessEqual(delta, 5)

    def test_mcnemar_exact_result(self) -> None:
        result = compute_mcnemar(
            [False, False, True, True],
            [True, True, True, False],
            mode_a="corrected_only",
            mode_b="contrastive",
        )

        self.assertEqual(result.n01, 2)
        self.assertEqual(result.n10, 1)
        self.assertGreaterEqual(result.p_value, 0.0)
        self.assertLessEqual(result.p_value, 1.0)

    def test_mcnemar_rejects_mismatched_lengths(self) -> None:
        with self.assertRaises(ValueError):
            compute_mcnemar([True], [], mode_a="a", mode_b="b")


if __name__ == "__main__":
    unittest.main()
