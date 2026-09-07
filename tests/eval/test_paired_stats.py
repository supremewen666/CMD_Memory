"""Paired-outcome statistics used by the arena analyzer and Route A.

Expected values are hand-calculated or taken from published worked examples,
never recomputed the way the implementation does.
"""

import math
import unittest

from cmd_audit.eval.paired_stats import (
    bootstrap_paired_diff,
    mcnemar_exact_p,
    sign_test_p,
    wilson_interval,
)


class McNemarTest(unittest.TestCase):
    def test_no_discordant_pairs_cannot_reject(self):
        self.assertEqual(mcnemar_exact_p(0, 0), 1.0)

    def test_hand_calculated_small_sample(self):
        """b=1, c=5: 2 * P(X <= 1), X ~ Binomial(6, 0.5).

        P(X<=1) = (C(6,0) + C(6,1)) / 64 = 7/64, so p = 14/64 = 0.21875.
        """
        self.assertAlmostEqual(mcnemar_exact_p(1, 5), 0.21875, places=10)

    def test_is_symmetric_in_its_discordant_counts(self):
        self.assertEqual(mcnemar_exact_p(2, 9), mcnemar_exact_p(9, 2))

    def test_all_discordant_pairs_favor_one_arm(self):
        """b=0, c=8: 2 * P(X <= 0) = 2 * (1/256) = 0.0078125."""
        self.assertAlmostEqual(mcnemar_exact_p(0, 8), 0.0078125, places=10)

    def test_probability_never_exceeds_one(self):
        self.assertEqual(mcnemar_exact_p(3, 3), 1.0)


class SignTestTest(unittest.TestCase):
    def test_ties_are_discarded_before_the_test(self):
        """Only the sign of a non-zero paired difference carries information."""
        deltas = (0.0, 0.0, 0.5, 0.5, 0.5, -0.5)
        # 3 positive, 1 negative, 2 ties dropped -> same as mcnemar(3, 1).
        self.assertAlmostEqual(sign_test_p(deltas), mcnemar_exact_p(3, 1))

    def test_all_ties_cannot_reject(self):
        self.assertEqual(sign_test_p((0.0, 0.0, 0.0)), 1.0)

    def test_unanimous_direction_is_significant_at_eight_pairs(self):
        self.assertAlmostEqual(sign_test_p((1.0,) * 8), 0.0078125, places=10)


class WilsonTest(unittest.TestCase):
    def test_empty_sample_has_no_interval(self):
        self.assertEqual(wilson_interval(0, 0), (0.0, 0.0))

    def test_interval_brackets_the_point_estimate(self):
        low, high = wilson_interval(7, 10)
        self.assertLess(low, 0.7)
        self.assertGreater(high, 0.7)

    def test_interval_stays_inside_the_unit_range(self):
        low, high = wilson_interval(0, 5)
        self.assertGreaterEqual(low, 0.0)
        self.assertLessEqual(high, 1.0)
        low, high = wilson_interval(5, 5)
        self.assertLessEqual(high, 1.0)

    def test_endpoints_satisfy_the_defining_equation(self):
        """Wilson's endpoints are where the score statistic equals z.

        The implementation uses the solved closed form; this checks it against
        the definition it solves, which is an independent route to the answer.
        """
        successes, total, z = 15, 50, 1.96
        observed = successes / total
        for endpoint in wilson_interval(successes, total, z):
            standard_error = math.sqrt(endpoint * (1 - endpoint) / total)
            self.assertAlmostEqual(
                abs(observed - endpoint) / standard_error, z, places=9
            )


class BootstrapTest(unittest.TestCase):
    def test_zero_difference_interval_contains_zero(self):
        deltas = (0.5, -0.5) * 20
        diff, low, high = bootstrap_paired_diff(deltas, seed=42)
        self.assertAlmostEqual(diff, 0.0)
        self.assertLessEqual(low, 0.0)
        self.assertGreaterEqual(high, 0.0)

    def test_constant_difference_has_a_degenerate_interval(self):
        diff, low, high = bootstrap_paired_diff((0.25,) * 30, seed=42)
        self.assertAlmostEqual(diff, 0.25)
        self.assertAlmostEqual(low, 0.25)
        self.assertAlmostEqual(high, 0.25)

    def test_same_seed_reproduces_the_interval(self):
        deltas = (0.9, -0.2, 0.4, 0.0, -0.7, 0.3)
        self.assertEqual(
            bootstrap_paired_diff(deltas, seed=7),
            bootstrap_paired_diff(deltas, seed=7),
        )

    def test_empty_sample_yields_no_interval(self):
        self.assertEqual(bootstrap_paired_diff((), seed=42), (0.0, 0.0, 0.0))


class FamilyBlockedBootstrapTest(unittest.TestCase):
    def test_blocked_resampling_draws_whole_families(self):
        """Siblings share a fault, so a case-level bootstrap understates spread.

        Two families disagree in direction. Resampling families keeps each
        family's four siblings together, so the interval must reach both
        family means (+1 and -1) rather than concentrating near their average.
        """
        deltas = (1.0,) * 4 + (-1.0,) * 4
        families = ("f1",) * 4 + ("f2",) * 4
        _diff, low, high = bootstrap_paired_diff(
            deltas, seed=42, families=families
        )
        self.assertAlmostEqual(low, -1.0)
        self.assertAlmostEqual(high, 1.0)

    def test_family_count_must_match_delta_count(self):
        with self.assertRaises(ValueError):
            bootstrap_paired_diff((1.0, 2.0), seed=42, families=("f1",))


if __name__ == "__main__":
    unittest.main()
