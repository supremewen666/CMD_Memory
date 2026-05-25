import unittest

from cmd_audit import bootstrap_metric


class BootstrapMetricTest(unittest.TestCase):
    def test_returns_mean_and_reproducible_ci(self) -> None:
        scores = {"c1": 0.0, "c2": 1.0, "c3": 1.0, "c4": 0.0}

        first = bootstrap_metric(tuple(scores), scores.__getitem__, n_iters=200)
        second = bootstrap_metric(tuple(scores), scores.__getitem__, n_iters=200)

        self.assertEqual(first, second)
        mean, low, high = first
        self.assertEqual(mean, 0.5)
        self.assertLessEqual(low, mean)
        self.assertGreaterEqual(high, mean)

    def test_rejects_empty_case_ids(self) -> None:
        with self.assertRaises(ValueError):
            bootstrap_metric((), lambda _: 1.0)

    def test_rejects_non_positive_iterations(self) -> None:
        with self.assertRaises(ValueError):
            bootstrap_metric(("c1",), lambda _: 1.0, n_iters=0)


if __name__ == "__main__":
    unittest.main()
