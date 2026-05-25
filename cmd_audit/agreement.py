"""Agreement metrics for human/LLM adjudication checks."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence


def cohen_kappa(labels_a: Sequence[str], labels_b: Sequence[str]) -> float:
    """Compute Cohen's kappa for two equally sized label sequences."""
    if len(labels_a) != len(labels_b):
        raise ValueError("cohen_kappa requires equally sized label sequences")
    if not labels_a:
        raise ValueError("cohen_kappa requires at least one paired label")

    n = len(labels_a)
    observed = sum(1 for a, b in zip(labels_a, labels_b) if a == b) / n
    counts_a = Counter(labels_a)
    counts_b = Counter(labels_b)
    expected = sum(
        (counts_a[label] / n) * (counts_b[label] / n)
        for label in set(counts_a) | set(counts_b)
    )
    if expected == 1.0:
        return 1.0 if observed == 1.0 else 0.0
    return (observed - expected) / (1.0 - expected)
