#!/usr/bin/env python3
"""Poisoning-density sweep: where does each item-level detector break?

task.md 3.5.  MemAudit states its own boundary as *dense* poisoning: methods that
find the bad memory by looking for a structural anomaly assume the bad memory is
the minority.  This sweep drives poison density from 0% to 50% and measures three
decision rules on the same recall sets:

* ``minority_vote`` — the MCG-style minority assumption: whichever cluster is
  smaller is the poisoned one.  Included as the baseline whose assumption the
  sweep is designed to break.
* ``loo_reconstruction`` — CMD's item gate step ③ as it currently stands
  (``item_gate/loo.py``): contrast each item against a reconstruction built from
  *the rest of the store*.  Its reference is endogenous, so as density rises the
  reference is itself poisoned.
* ``anchored_contrast`` — the same directed contrast against an *exogenous*
  reference (``eval/anchor_discipline.py``'s anchored reference set).  The
  reference cannot be poisoned by the store, so density does not move it.

The finding this sweep is built to expose is a boundary on CMD, not only on the
baseline: reference-contrast is density-robust only when the reference is
exogenous to the poisoned store.  ``loo_reconstruction`` and ``minority_vote``
are expected to fail in different ways — the former degrades, the latter inverts
and starts flagging the clean items once the poison becomes the majority.

**Scope, stated plainly.** Divergence here is a deterministic lexical agreement
oracle, not the G-Eval judge ``item_gate/divergence.py`` calls at runtime.  What
is being measured is therefore the *decision rule's* dependence on density, with
judge noise held at zero — a necessary condition, not a claim about end-to-end
detector accuracy with a real judge.  A judge-in-the-loop replication is a
separate experiment; a rule that already fails here cannot be rescued by a better
judge, which is what makes the zero-call version worth running first.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
from statistics import fmean
from typing import Callable, Sequence

from cmd_audit.repair.ghost_ecology import content_sha256


SWEEP_SCHEMA_VERSION = "cmd-poison-density-sweep-v1"
DETECTORS: tuple[str, ...] = (
    "minority_vote",
    "loo_reconstruction",
    "anchored_contrast",
)
# Above this share of the recall set the poison is no longer a minority, which is
# the assumption `minority_vote` rests on.
MINORITY_ASSUMPTION_LIMIT = 0.5


class PoisonSweepError(ValueError):
    """Raised when a sweep configuration cannot produce a usable grid."""


@dataclass(frozen=True)
class SweepItem:
    """One memory item in a recall set, with its ground-truth poison flag.

    ``poisoned`` is the sweep's construction record used to score the detectors.
    No detector reads it — :func:`_detect` receives texts only.
    """

    memory_id: str
    text: str
    poisoned: bool


@dataclass(frozen=True)
class SweepCase:
    case_id: str
    query: str
    items: tuple[SweepItem, ...]
    anchor_text: str

    def __post_init__(self) -> None:
        if len(self.items) < 2:
            raise PoisonSweepError("a recall set needs at least two items")
        if not self.anchor_text:
            raise PoisonSweepError("anchored contrast requires a reference text")

    @property
    def density(self) -> float:
        return sum(1 for row in self.items if row.poisoned) / len(self.items)


def _tokens(text: str) -> frozenset[str]:
    return frozenset(token for token in text.lower().split() if token)


def _agreement(left: str, right: str) -> float:
    """Deterministic stand-in for directed entailment: token Jaccard in [0, 1]."""
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def build_sweep_case(
    *,
    case_id: str,
    recall_size: int,
    poisoned_count: int,
    clean_claim: str = "the deploy key rotated on the fourth of March",
    poison_claim: str = "the deploy key rotated on the ninth of September",
) -> SweepCase:
    """Build one coordinated-poisoning recall set.

    The poisoned items agree with *each other* on a single false claim rather
    than each being independently corrupt.  That is the regime that defeats a
    minority assumption: uncoordinated noise stays a scattered minority at any
    density, so it would make the baseline look robust for the wrong reason.
    """
    if recall_size < 2:
        raise PoisonSweepError("recall_size must be at least 2")
    if not 0 <= poisoned_count <= recall_size:
        raise PoisonSweepError("poisoned_count must lie in [0, recall_size]")
    items: list[SweepItem] = []
    for index in range(recall_size):
        poisoned = index < poisoned_count
        claim = poison_claim if poisoned else clean_claim
        items.append(
            SweepItem(
                memory_id=f"{case_id}-m{index}",
                # A per-item suffix keeps items non-identical, so a detector
                # cannot succeed by exact-duplicate matching alone.
                text=f"{claim} (note {index})",
                poisoned=poisoned,
            )
        )
    return SweepCase(
        case_id=case_id,
        query="when did the deploy key rotate?",
        items=tuple(items),
        anchor_text=clean_claim,
    )


def _minority_vote(case: SweepCase, *, threshold: float) -> tuple[bool, ...]:
    """Flag the smaller agreement cluster — the structural-anomaly assumption."""
    texts = [row.text for row in case.items]
    clusters: list[list[int]] = []
    for index, text in enumerate(texts):
        for cluster in clusters:
            if _agreement(text, texts[cluster[0]]) >= threshold:
                cluster.append(index)
                break
        else:
            clusters.append([index])
    if len(clusters) < 2:
        return tuple(False for _ in texts)
    smallest = min(clusters, key=len)
    largest = max(clusters, key=len)
    if len(smallest) == len(largest):
        # A tie gives the rule nothing to pick; abstaining is the honest read.
        return tuple(False for _ in texts)
    flagged = set(smallest)
    return tuple(index in flagged for index in range(len(texts)))


def _loo_reconstruction(case: SweepCase, *, threshold: float) -> tuple[bool, ...]:
    """Contrast each item against a reconstruction from the rest of the store.

    The reconstruction is the majority claim among the remaining items, which is
    exactly why this degrades with density: once the poison is the majority, the
    reference reconstructs the poison.
    """
    texts = [row.text for row in case.items]
    flags: list[bool] = []
    for index, text in enumerate(texts):
        others = [texts[other] for other in range(len(texts)) if other != index]
        reference = _majority_text(others, threshold=threshold)
        flags.append(_agreement(text, reference) < threshold)
    return tuple(flags)


def _anchored_contrast(case: SweepCase, *, threshold: float) -> tuple[bool, ...]:
    """Contrast each item against the exogenous anchored reference."""
    return tuple(
        _agreement(row.text, case.anchor_text) < threshold for row in case.items
    )


def _majority_text(texts: Sequence[str], *, threshold: float) -> str:
    """The text whose agreement cluster is largest; ties break on first seen."""
    if not texts:
        return ""
    best_index, best_size = 0, -1
    for index, text in enumerate(texts):
        size = sum(1 for other in texts if _agreement(text, other) >= threshold)
        if size > best_size:
            best_index, best_size = index, size
    return texts[best_index]


_DETECTOR_FNS: dict[str, Callable[..., tuple[bool, ...]]] = {
    "minority_vote": _minority_vote,
    "loo_reconstruction": _loo_reconstruction,
    "anchored_contrast": _anchored_contrast,
}


def _detect(detector: str, case: SweepCase, *, threshold: float) -> tuple[bool, ...]:
    if detector not in _DETECTOR_FNS:
        raise PoisonSweepError(f"unknown detector: {detector}")
    return _DETECTOR_FNS[detector](case, threshold=threshold)


@dataclass(frozen=True)
class DetectorScore:
    detector: str
    density: float
    true_positive: int
    false_positive: int
    true_negative: int
    false_negative: int

    @property
    def precision(self) -> float:
        denominator = self.true_positive + self.false_positive
        return self.true_positive / denominator if denominator else 0.0

    @property
    def recall(self) -> float:
        denominator = self.true_positive + self.false_negative
        return self.true_positive / denominator if denominator else 0.0

    @property
    def f1(self) -> float:
        if not self.precision or not self.recall:
            return 0.0
        return 2 * self.precision * self.recall / (self.precision + self.recall)

    @property
    def inverted(self) -> bool:
        """True when the rule flagged more clean items than poisoned ones.

        This is the failure mode worth separating out: an inverted detector does
        not merely miss the poison, it recommends repairing the healthy items.
        """
        return self.false_positive > self.true_positive

    def to_mapping(self) -> dict[str, object]:
        return {
            "detector": self.detector,
            "density": self.density,
            "true_positive": self.true_positive,
            "false_positive": self.false_positive,
            "true_negative": self.true_negative,
            "false_negative": self.false_negative,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "inverted": self.inverted,
        }


def score_detector(
    detector: str, cases: Sequence[SweepCase], *, threshold: float
) -> DetectorScore:
    if not cases:
        raise PoisonSweepError("scoring requires at least one case")
    densities = {case.density for case in cases}
    if len(densities) != 1:
        raise PoisonSweepError("a scored cell must hold one density")
    counts = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    for case in cases:
        flags = _detect(detector, case, threshold=threshold)
        for item, flagged in zip(case.items, flags, strict=True):
            if flagged and item.poisoned:
                counts["tp"] += 1
            elif flagged:
                counts["fp"] += 1
            elif item.poisoned:
                counts["fn"] += 1
            else:
                counts["tn"] += 1
    return DetectorScore(
        detector=detector,
        density=densities.pop(),
        true_positive=counts["tp"],
        false_positive=counts["fp"],
        true_negative=counts["tn"],
        false_negative=counts["fn"],
    )


def run_sweep(
    *,
    recall_size: int = 10,
    max_density: float = 0.9,
    threshold: float = 0.6,
    detectors: Sequence[str] = DETECTORS,
    cases_per_cell: int = 5,
) -> dict[str, object]:
    """Sweep poison density and score every detector on identical recall sets.

    ``max_density`` defaults past 0.5 deliberately.  task.md 3.5 asks for a 0–50%
    grid, but the minority assumption only *breaks* above 50%: a sweep that stops
    at the boundary shows all three rules agreeing and reports no separation.
    The cells above the limit are what distinguish an endogenous reference from an
    exogenous one.
    """
    if recall_size < 2:
        raise PoisonSweepError("recall_size must be at least 2")
    if not 0.0 < max_density <= 1.0:
        raise PoisonSweepError("max_density must lie in (0, 1]")
    if cases_per_cell < 1:
        raise PoisonSweepError("cases_per_cell must be positive")
    unknown = sorted(set(detectors) - set(DETECTORS))
    if unknown:
        raise PoisonSweepError(f"unknown detector: {unknown}")

    max_poisoned = int(max_density * recall_size)
    rows: list[dict[str, object]] = []
    for poisoned_count in range(max_poisoned + 1):
        cases = tuple(
            build_sweep_case(
                case_id=f"d{poisoned_count}c{index}",
                recall_size=recall_size,
                poisoned_count=poisoned_count,
            )
            for index in range(cases_per_cell)
        )
        for detector in detectors:
            rows.append(
                score_detector(detector, cases, threshold=threshold).to_mapping()
            )

    by_detector: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        by_detector.setdefault(str(row["detector"]), []).append(row)

    summary: dict[str, object] = {}
    for detector, detector_rows in by_detector.items():
        scored = [row for row in detector_rows if float(row["density"]) > 0.0]
        inverted = [
            float(row["density"]) for row in detector_rows if bool(row["inverted"])
        ]
        summary[detector] = {
            "mean_f1_over_poisoned_cells": (
                fmean(float(row["f1"]) for row in scored) if scored else 0.0
            ),
            "min_f1_over_poisoned_cells": (
                min(float(row["f1"]) for row in scored) if scored else 0.0
            ),
            "first_inverted_density": min(inverted) if inverted else None,
            "clean_cell_false_positives": next(
                int(row["false_positive"])
                for row in detector_rows
                if float(row["density"]) == 0.0
            ),
        }

    payload: dict[str, object] = {
        "schema_version": SWEEP_SCHEMA_VERSION,
        "model_calls": 0,
        "divergence_oracle": "deterministic_lexical_agreement_not_geval_judge",
        "recall_size": recall_size,
        "max_density": max_density,
        "agreement_threshold": threshold,
        "cases_per_cell": cases_per_cell,
        "poisoning_regime": "coordinated_single_false_claim",
        "minority_assumption_limit": MINORITY_ASSUMPTION_LIMIT,
        "detectors": list(detectors),
        "swept_past_minority_limit": max_density > MINORITY_ASSUMPTION_LIMIT,
        "grid": rows,
        "summary": summary,
    }
    return {**payload, "report_sha256": content_sha256(payload)}


def format_grid(report: dict[str, object]) -> str:
    """Render the density x detector grid as a fixed-width table."""
    rows = report["grid"]
    assert isinstance(rows, list)
    detectors = [str(name) for name in report["detectors"]]  # type: ignore[union-attr]
    densities = sorted({float(row["density"]) for row in rows})
    width = max(len(name) for name in detectors) + 2
    header = "density".ljust(9) + "".join(name.ljust(width) for name in detectors)
    lines = [header, "-" * len(header)]
    for density in densities:
        cells = []
        for detector in detectors:
            row = next(
                item
                for item in rows
                if float(item["density"]) == density
                and str(item["detector"]) == detector
            )
            if density == 0.0:
                # No poisoned item exists, so F1 has no positives to score and
                # would print as 0.00 — indistinguishable from total failure.
                # The false-positive count is the only meaningful number here.
                cell = f"fp={int(row['false_positive'])}"
            else:
                mark = "!" if bool(row["inverted"]) else ""
                cell = f"{float(row['f1']):.2f}{mark}"
            cells.append(cell.ljust(width))
        lines.append(f"{density:<9.2f}" + "".join(cells))
    lines.append("")
    lines.append("F1 per cell; '!' marks an inverted detector (flagged more clean")
    lines.append("items than poisoned ones — it would repair the healthy memory).")
    lines.append("At density 0.00 no positives exist, so the cell reports false")
    lines.append("positives on a clean store instead of an undefined F1.")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--recall-size", type=int, default=10)
    parser.add_argument("--max-density", type=float, default=0.9)
    parser.add_argument("--threshold", type=float, default=0.6)
    parser.add_argument("--cases-per-cell", type=int, default=5)
    args = parser.parse_args(argv)

    if args.output.exists():
        raise PoisonSweepError(f"refusing to overwrite sweep report: {args.output}")
    report = run_sweep(
        recall_size=args.recall_size,
        max_density=args.max_density,
        threshold=args.threshold,
        cases_per_cell=args.cases_per_cell,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(format_grid(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DETECTORS",
    "MINORITY_ASSUMPTION_LIMIT",
    "SWEEP_SCHEMA_VERSION",
    "DetectorScore",
    "PoisonSweepError",
    "SweepCase",
    "SweepItem",
    "build_sweep_case",
    "format_grid",
    "main",
    "run_sweep",
    "score_detector",
]
