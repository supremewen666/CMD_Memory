"""Mechanical confirmatory gates for audited niche evolution."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import fmean
import math
import random
from typing import Iterable, Sequence


@dataclass(frozen=True)
class NicheConfirmatoryOutcome:
    case_id: str
    family_id: str
    arm_id: str
    recovery_gain: float
    run_seed: int = 0
    scope_external: bool = False
    unseen_family: bool = False
    null_or_fill: bool = False
    selection_matches_frozen: bool = True
    anchor_regression: bool = False
    budget_aligned: bool = True
    runtime_forbidden_fields_ok: bool = True

    def __post_init__(self) -> None:
        if not self.case_id or not self.family_id or not self.arm_id:
            raise ValueError("case, family, and arm ids are required")
        if not math.isfinite(self.recovery_gain):
            raise ValueError("recovery_gain must be finite")


@dataclass(frozen=True)
class NicheContrast:
    treatment_arm: str
    control_arm: str
    estimate: float
    lower_bound_95_one_sided: float
    families: int
    passed: bool


@dataclass(frozen=True)
class NicheConfirmationDecision:
    gstar: str
    vs_all_frozen: NicheContrast
    vs_unkeyed_pool: NicheContrast
    graph_increment: NicheContrast | None
    scope_external_lower_bound: float | None
    unseen_family_lower_bound: float | None
    null_fill_exact: bool
    anchor_regressions: int
    budget_alignment_rate: float
    forbidden_field_assertion_rate: float
    primary_passed: bool
    graph_claim_passed: bool
    final_decision: str
    bootstrap_samples: int
    bootstrap_seed: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def evaluate_niche_confirmation(
    outcomes: Iterable[NicheConfirmatoryOutcome],
    *,
    gstar: str,
    safety_margin: float = -0.05,
    bootstrap_samples: int = 10_000,
    bootstrap_seed: int = 0,
) -> NicheConfirmationDecision:
    rows = tuple(outcomes)
    if not rows:
        raise ValueError("no niche confirmatory outcomes")
    if gstar not in {"G2", "G3"}:
        raise ValueError("gstar must be G2 or G3")
    if bootstrap_samples < 100:
        raise ValueError("bootstrap_samples must be >= 100")
    _validate_unique(rows)
    required = {gstar, "all_frozen", "unkeyed_pool"}
    if gstar == "G3":
        required.add("G2")
    if not required.issubset({row.arm_id for row in rows}):
        raise ValueError("missing primary confirmatory arms")

    eligible = tuple(row for row in rows if not row.null_or_fill)
    vs_frozen = _contrast(
        eligible,
        treatment=gstar,
        control="all_frozen",
        samples=bootstrap_samples,
        seed=bootstrap_seed,
    )
    vs_unkeyed = _contrast(
        eligible,
        treatment=gstar,
        control="unkeyed_pool",
        samples=bootstrap_samples,
        seed=bootstrap_seed + 1,
    )
    graph_increment = (
        _contrast(
            eligible,
            treatment="G3",
            control="G2",
            samples=bootstrap_samples,
            seed=bootstrap_seed + 2,
        )
        if gstar == "G3"
        else None
    )
    scope_external_lower = _safety_lower(
        rows,
        gstar=gstar,
        predicate=lambda row: row.scope_external,
        samples=bootstrap_samples,
        seed=bootstrap_seed + 3,
    )
    unseen_lower = _safety_lower(
        rows,
        gstar=gstar,
        predicate=lambda row: row.unseen_family,
        samples=bootstrap_samples,
        seed=bootstrap_seed + 4,
    )
    null_rows = tuple(row for row in rows if row.null_or_fill)
    null_fill_exact = bool(null_rows) and all(
        row.selection_matches_frozen for row in null_rows
    )
    anchor_regressions = sum(
        row.anchor_regression for row in rows if row.arm_id == gstar
    )
    budget_alignment_rate = sum(row.budget_aligned for row in rows) / len(rows)
    forbidden_rate = sum(
        row.runtime_forbidden_fields_ok for row in rows
    ) / len(rows)
    safety_passed = (
        scope_external_lower is not None
        and scope_external_lower >= safety_margin
        and unseen_lower is not None
        and unseen_lower >= safety_margin
        and null_fill_exact
        and anchor_regressions == 0
        and budget_alignment_rate == 1.0
        and forbidden_rate == 1.0
    )
    primary_passed = (
        vs_frozen.passed and vs_unkeyed.passed and safety_passed
    )
    graph_claim_passed = (
        graph_increment is not None
        and graph_increment.passed
        and primary_passed
    )
    final_decision = (
        "positive_niche_and_graph_claim"
        if graph_claim_passed
        else (
            "positive_niche_claim"
            if primary_passed
            else "negative_or_partial_result"
        )
    )
    return NicheConfirmationDecision(
        gstar=gstar,
        vs_all_frozen=vs_frozen,
        vs_unkeyed_pool=vs_unkeyed,
        graph_increment=graph_increment,
        scope_external_lower_bound=scope_external_lower,
        unseen_family_lower_bound=unseen_lower,
        null_fill_exact=null_fill_exact,
        anchor_regressions=anchor_regressions,
        budget_alignment_rate=budget_alignment_rate,
        forbidden_field_assertion_rate=forbidden_rate,
        primary_passed=primary_passed,
        graph_claim_passed=graph_claim_passed,
        final_decision=final_decision,
        bootstrap_samples=bootstrap_samples,
        bootstrap_seed=bootstrap_seed,
    )


def _contrast(
    rows: Sequence[NicheConfirmatoryOutcome],
    *,
    treatment: str,
    control: str,
    samples: int,
    seed: int,
) -> NicheContrast:
    values = _paired_family_differences(rows, treatment, control)
    estimate = fmean(value for _family, value in values)
    lower = _family_blocked_lower(
        values,
        samples=samples,
        seed=seed,
    )
    return NicheContrast(
        treatment,
        control,
        estimate,
        lower,
        len(values),
        estimate > 0.0 and lower > 0.0,
    )


def _safety_lower(
    rows: Sequence[NicheConfirmatoryOutcome],
    *,
    gstar: str,
    predicate,
    samples: int,
    seed: int,
) -> float | None:
    subset = tuple(row for row in rows if predicate(row))
    if not subset:
        return None
    values = _paired_family_differences(subset, gstar, "all_frozen")
    return _family_blocked_lower(values, samples=samples, seed=seed)


def _paired_family_differences(
    rows: Sequence[NicheConfirmatoryOutcome],
    treatment: str,
    control: str,
) -> tuple[tuple[str, float], ...]:
    by_case = {
        (row.family_id, row.case_id, row.run_seed, row.arm_id): row.recovery_gain
        for row in rows
        if row.arm_id in {treatment, control}
    }
    cases = sorted(
        {
            (family_id, case_id, run_seed)
            for family_id, case_id, run_seed, _arm in by_case
        }
    )
    missing = [
        (family_id, case_id, run_seed)
        for family_id, case_id, run_seed in cases
        if (family_id, case_id, run_seed, treatment) not in by_case
        or (family_id, case_id, run_seed, control) not in by_case
    ]
    if missing:
        raise ValueError(
            f"incomplete paired outcomes for {treatment}/{control}: "
            f"{missing[:3]}"
        )
    grouped: dict[str, list[float]] = {}
    for family_id, case_id, run_seed in cases:
        grouped.setdefault(family_id, []).append(
            by_case[(family_id, case_id, run_seed, treatment)]
            - by_case[(family_id, case_id, run_seed, control)]
        )
    if not grouped:
        raise ValueError(f"no paired outcomes for {treatment}/{control}")
    return tuple(
        (family_id, fmean(grouped[family_id]))
        for family_id in sorted(grouped)
    )


def _family_blocked_lower(
    values: Sequence[tuple[str, float]],
    *,
    samples: int,
    seed: int,
) -> float:
    family_values = tuple(value for _family, value in values)
    rng = random.Random(seed)
    draws = sorted(
        fmean(
            family_values[rng.randrange(len(family_values))]
            for _ in family_values
        )
        for _ in range(samples)
    )
    index = max(0, min(len(draws) - 1, int(0.05 * len(draws))))
    return draws[index]


def _validate_unique(rows: Sequence[NicheConfirmatoryOutcome]) -> None:
    keys: set[tuple[str, str, int]] = set()
    for row in rows:
        key = (row.case_id, row.arm_id, row.run_seed)
        if key in keys:
            raise ValueError(f"duplicate confirmatory outcome: {key}")
        keys.add(key)
