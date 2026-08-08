"""Paired-outcome significance primitives (stdlib only).

The arena compares two arms on the same case, so every outcome is a matched
pair and the useful tests are paired ones: an exact McNemar/sign test over
discordant pairs, and a bootstrap interval over paired differences.

Family-blocked resampling exists because sibling cases in one family share an
injected fault. Treating them as independent draws understates the spread, so
``bootstrap_paired_diff`` can resample whole families instead of single cases.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence

__all__ = [
    "BOOTSTRAP_ITERATIONS",
    "bootstrap_paired_diff",
    "mcnemar_exact_p",
    "sign_test_p",
    "wilson_interval",
]

BOOTSTRAP_ITERATIONS = 2000


def mcnemar_exact_p(wins_a: int, wins_b: int) -> float:
    """Exact two-sided McNemar p-value over discordant counts.

    Concordant pairs carry no information about which arm is better, so only
    the discordant ones enter: under the null, ``wins_a ~ Binomial(n, 0.5)``
    where ``n = wins_a + wins_b``. Exact rather than chi-square because arena
    strata can be small enough for the asymptotic form to mislead.
    """
    total = wins_a + wins_b
    if total == 0:
        return 1.0
    smaller = min(wins_a, wins_b)
    tail = sum(math.comb(total, i) for i in range(smaller + 1)) * (0.5**total)
    return min(1.0, 2.0 * tail)


def sign_test_p(deltas: Sequence[float]) -> float:
    """Exact two-sided sign test over paired differences.

    Ties are dropped rather than split: a zero difference is evidence for
    neither arm, and counting it as half a win in each direction would inflate
    the sample the test believes it has.
    """
    positive = sum(1 for delta in deltas if delta > 0)
    negative = sum(1 for delta in deltas if delta < 0)
    return mcnemar_exact_p(positive, negative)


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if total == 0:
        return (0.0, 0.0)
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    half_width = (
        z
        * math.sqrt(
            proportion * (1 - proportion) / total + z * z / (4 * total * total)
        )
    ) / denominator
    return (max(0.0, center - half_width), min(1.0, center + half_width))


def bootstrap_paired_diff(
    deltas: Sequence[float],
    *,
    seed: int,
    families: Sequence[str] | None = None,
    iterations: int = BOOTSTRAP_ITERATIONS,
) -> tuple[float, float, float]:
    """Percentile bootstrap of the mean paired difference.

    Returns ``(mean, ci_low, ci_high)``. With ``families`` given, resampling
    draws families with replacement and keeps each family's cases together.
    """
    count = len(deltas)
    if count == 0:
        return (0.0, 0.0, 0.0)
    if families is not None and len(families) != count:
        raise ValueError(
            f"families has {len(families)} entries for {count} deltas"
        )
    mean = sum(deltas) / count
    rng = random.Random(seed)

    if families is None:
        blocks: list[Sequence[float]] = [(delta,) for delta in deltas]
    else:
        grouped: dict[str, list[float]] = {}
        for family, delta in zip(families, deltas):
            grouped.setdefault(family, []).append(delta)
        blocks = [tuple(values) for _family, values in sorted(grouped.items())]

    block_count = len(blocks)
    means: list[float] = []
    for _ in range(iterations):
        total = 0.0
        drawn = 0
        for _ in range(block_count):
            block = blocks[rng.randrange(block_count)]
            total += sum(block)
            drawn += len(block)
        means.append(total / drawn)
    return (mean, _percentile(means, 2.5), _percentile(means, 97.5))


def _percentile(values: list[float], percentile: float) -> float:
    """Linear-interpolation percentile, matching numpy's default."""
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight
