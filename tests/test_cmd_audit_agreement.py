import unittest

from cmd_audit.agreement import cohen_kappa


class CohenKappaTest(unittest.TestCase):
    def test_perfect_agreement_is_one(self) -> None:
        labels = ["retrieval_error", "write_error", "route_error"]
        self.assertEqual(cohen_kappa(labels, labels), 1.0)

    def test_partial_agreement_is_between_zero_and_one(self) -> None:
        score = cohen_kappa(
            ["a", "a", "b", "b"],
            ["a", "b", "b", "b"],
        )

        self.assertGreater(score, 0.0)
        self.assertLess(score, 1.0)

    def test_mismatched_lengths_rejected(self) -> None:
        with self.assertRaises(ValueError):
            cohen_kappa(["a"], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
