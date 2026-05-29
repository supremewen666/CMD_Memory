"""Bootstrap confidence intervals for Decision 34 paper metrics."""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence


def bootstrap_metric(
    case_ids: Sequence[str],
    score_fn: Callable[[str], float],
    n_iters: int = 1000,
) -> tuple[float, float, float]:
    """Return ``(mean, ci_low, ci_high)`` over case-level bootstrap samples.

    ``score_fn`` is evaluated once per original case ID, then bootstrap samples
    resample those per-case scores with replacement. The RNG seed is fixed so
    paper tables are reproducible unless the input scores change.
    """
    if not case_ids:
        raise ValueError("bootstrap_metric requires at least one case_id")
    if n_iters <= 0:
        raise ValueError("bootstrap_metric requires n_iters > 0")

    scores = [float(score_fn(case_id)) for case_id in case_ids]
    n = len(scores)
    mean = sum(scores) / n
    rng = random.Random(42)
    boot_means: list[float] = []
    for _ in range(n_iters):
        boot_means.append(sum(scores[rng.randrange(n)] for _ in range(n)) / n)
    boot_means.sort()
    low_index = int(0.025 * (n_iters - 1))
    high_index = int(0.975 * (n_iters - 1))
    return mean, boot_means[low_index], boot_means[high_index]
