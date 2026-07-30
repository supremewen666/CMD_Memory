"""Family-blocked Gates and split utilities for Skill evolution experiments."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import random
from statistics import fmean
from typing import Iterable, Mapping, Sequence


CHECKPOINTS = ("L0", "L1", "L2", "L3")
REPRESENTED_ARMS = ("patterned", "unkeyed_global", "no_update")


@dataclass(frozen=True)
class FamilySplitEntry:
    recurrent_family_id: str
    bucket: int
    role: str
    update_variant_indices: tuple[int, ...]
    probe_variant_indices: tuple[int, ...]


@dataclass(frozen=True)
class EvolutionProbeOutcome:
    recurrent_family_id: str
    recurrent_variant_index: int
    probe_set: str
    checkpoint: str
    arm_id: str
    recovered: bool
    run_seed: int = 0


@dataclass(frozen=True)
class ContrastResult:
    estimate: float
    lower_bound_95_one_sided: float
    passed: bool


@dataclass(frozen=True)
class PrimaryGateResult:
    endpoint: ContrastResult
    difference_in_differences: ContrastResult
    aulc: ContrastResult
    passed: bool


@dataclass(frozen=True)
class SafetyGateResult:
    estimate: float
    lower_bound_95_one_sided: float
    margin: float
    passed: bool


@dataclass(frozen=True)
class FamilyNetGains:
    """One memtrace family's baseline vs. later net-gain observations.

    ``baseline_gains`` holds the net_gain of every member with ``c_index ==
    0``; ``later_gains`` holds the net_gain of every member with ``c_index >
    0``. A family missing either side is excluded from the within-family
    gate (see :func:`evaluate_within_family_gate`), never padded.
    """

    family_id: str
    keying: str  # "kp" | "slug"
    baseline_gains: tuple[float, ...]
    later_gains: tuple[float, ...]


@dataclass(frozen=True)
class WithinFamilyResult:
    keying: str  # "kp" | "slug" | "combined"
    n_families: int
    mean_marginal_gain: float
    lower_bound_95: float
    passed: bool
    excluded_families: int


@dataclass(frozen=True)
class WithinFamilyReview:
    kp: WithinFamilyResult
    slug: WithinFamilyResult
    combined: WithinFamilyResult
    strata_disagree: bool


@dataclass(frozen=True)
class EvolutionGateResults:
    primary: PrimaryGateResult
    safety: SafetyGateResult
    bootstrap_samples: int
    bootstrap_seed: int
    checkpoint_rates: tuple[tuple[str, str, str, float], ...]
    worst_family_changes: tuple[tuple[str, str, float], ...]
    within_family: WithinFamilyReview


def family_bucket(recurrent_family_id: str) -> int:
    """Hash-stable split independent of stream or Python hash seeds."""
    digest = hashlib.sha256(recurrent_family_id.encode("utf-8")).hexdigest()
    return int(digest, 16) % 5


def build_family_split(
    cases: Iterable[Mapping[str, object]],
) -> tuple[FamilySplitEntry, ...]:
    """Build and validate the fixed represented/unseen family split."""
    variants: dict[str, set[int]] = {}
    for case in cases:
        family_id = str(case.get("recurrent_family_id") or "")
        if not family_id:
            raise ValueError("every evolution case requires recurrent_family_id")
        try:
            variant_index = int(case["recurrent_variant_index"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"{family_id}: invalid recurrent_variant_index"
            ) from exc
        variants.setdefault(family_id, set()).add(variant_index)
    result: list[FamilySplitEntry] = []
    for family_id in sorted(variants):
        if variants[family_id] != {0, 1, 2, 3, 4}:
            raise ValueError(
                f"{family_id}: expected variants 0..4, got "
                f"{sorted(variants[family_id])}"
            )
        bucket = family_bucket(family_id)
        result.append(
            FamilySplitEntry(
                recurrent_family_id=family_id,
                bucket=bucket,
                role="unseen" if bucket == 0 else "represented",
                update_variant_indices=() if bucket == 0 else (0, 1, 2),
                probe_variant_indices=(0, 1, 2, 3, 4)
                if bucket == 0
                else (3, 4),
            )
        )
    return tuple(result)


def write_family_split(
    path: str | Path,
    entries: Sequence[FamilySplitEntry],
) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            [asdict(item) for item in entries],
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return output


def normalized_trapezoid(values: Sequence[float]) -> float:
    if len(values) < 2:
        raise ValueError("AULC requires at least two checkpoints")
    return sum(
        (float(left) + float(right)) / 2.0
        for left, right in zip(values, values[1:])
    ) / (len(values) - 1)


def evaluate_evolution_gates(
    outcomes: Iterable[EvolutionProbeOutcome],
    *,
    within_family_gains: Iterable[FamilyNetGains],
    bootstrap_samples: int = 10_000,
    bootstrap_seed: int = 24,
    safety_margin: float = -0.05,
) -> EvolutionGateResults:
    """Evaluate the conjunctive represented Gate and unseen safety Gate.

    Variants and seeds are first averaged within a family.  Bootstrap samples
    then draw whole family blocks, keeping every arm and checkpoint paired.
    """
    if bootstrap_samples < 100:
        raise ValueError("bootstrap_samples must be at least 100")
    rows = tuple(outcomes)
    _validate_rows(rows)
    represented = _family_blocks(rows, "represented")
    unseen = _family_blocks(rows, "unseen")
    if not represented or not unseen:
        raise ValueError("both represented and unseen probe sets are required")

    primary_point = _primary_contrasts(represented)
    safety_point = _safety_contrast(unseen)
    rng = random.Random(bootstrap_seed)
    primary_draws = [[], [], []]
    safety_draws: list[float] = []
    represented_ids = tuple(sorted(represented))
    unseen_ids = tuple(sorted(unseen))
    for _ in range(bootstrap_samples):
        sampled_represented = [
            represented[represented_ids[rng.randrange(len(represented_ids))]]
            for _ in represented_ids
        ]
        values = _primary_contrasts_from_blocks(sampled_represented)
        for index, value in enumerate(values):
            primary_draws[index].append(value)
        sampled_unseen = [
            unseen[unseen_ids[rng.randrange(len(unseen_ids))]]
            for _ in unseen_ids
        ]
        safety_draws.append(_safety_contrast_from_blocks(sampled_unseen))

    primary_lbs = tuple(_one_sided_lower(draws) for draws in primary_draws)
    endpoint = ContrastResult(
        primary_point[0], primary_lbs[0], primary_lbs[0] > 0.0
    )
    did = ContrastResult(
        primary_point[1], primary_lbs[1], primary_lbs[1] > 0.0
    )
    aulc = ContrastResult(
        primary_point[2], primary_lbs[2], primary_lbs[2] > 0.0
    )
    existing_triple_passed = endpoint.passed and did.passed and aulc.passed
    within_family = evaluate_within_family_gate(
        within_family_gains,
        bootstrap_samples=bootstrap_samples,
        bootstrap_seed=bootstrap_seed,
    )
    primary = PrimaryGateResult(
        endpoint=endpoint,
        difference_in_differences=did,
        aulc=aulc,
        passed=existing_triple_passed and within_family.combined.passed,
    )
    safety_lb = _one_sided_lower(safety_draws)
    safety = SafetyGateResult(
        estimate=safety_point,
        lower_bound_95_one_sided=safety_lb,
        margin=safety_margin,
        passed=safety_point >= 0.0 and safety_lb >= safety_margin,
    )
    rates = tuple(
        (
            probe_set,
            checkpoint,
            arm,
            _mean_block_value(blocks, arm, checkpoint),
        )
        for probe_set, blocks in (
            ("represented", represented),
            ("unseen", unseen),
        )
        for checkpoint in CHECKPOINTS
        for arm in REPRESENTED_ARMS
    )
    worst = tuple(
        (
            checkpoint,
            family_id,
            blocks[family_id][("patterned", checkpoint)]
            - blocks[family_id][("no_update", checkpoint)],
        )
        for checkpoint in CHECKPOINTS
        for blocks in (unseen,)
        for family_id in (
            min(
                blocks,
                key=lambda item: (
                    blocks[item][("patterned", checkpoint)]
                    - blocks[item][("no_update", checkpoint)],
                    item,
                ),
            ),
        )
    )
    return EvolutionGateResults(
        primary=primary,
        safety=safety,
        bootstrap_samples=bootstrap_samples,
        bootstrap_seed=bootstrap_seed,
        checkpoint_rates=rates,
        worst_family_changes=worst,
        within_family=within_family,
    )


def evaluate_within_family_gate(
    families: Iterable[FamilyNetGains],
    *,
    bootstrap_samples: int = 10_000,
    bootstrap_seed: int = 24,
) -> WithinFamilyReview:
    """Within-family marginal utility gate (amends CONTRACT SS8).

    For each family with at least one baseline (c_index==0) member and at
    least one later (c_index>0) member, the marginal gain is
    ``mean(later_gains) - mean(baseline_gains)``. Families missing either
    side are excluded and counted, never padded or raised on.

    The paired resample unit is the family. Reuses the existing one-sided
    paired bootstrap (`_one_sided_lower`) rather than a second bootstrap
    implementation. Reports three stratified results -- kp, slug, combined
    -- with the gate verdict carried on `combined` only; kp/slug are
    reported for direction agreement and are not gates themselves.
    """
    all_families = tuple(families)
    kp = _within_family_stratum(
        [f for f in all_families if f.keying == "kp"],
        keying="kp",
        bootstrap_samples=bootstrap_samples,
        bootstrap_seed=bootstrap_seed,
    )
    slug = _within_family_stratum(
        [f for f in all_families if f.keying == "slug"],
        keying="slug",
        bootstrap_samples=bootstrap_samples,
        bootstrap_seed=bootstrap_seed,
    )
    combined = _within_family_stratum(
        list(all_families),
        keying="combined",
        bootstrap_samples=bootstrap_samples,
        bootstrap_seed=bootstrap_seed,
    )
    strata_disagree = (
        kp.n_families > 0
        and slug.n_families > 0
        and (kp.mean_marginal_gain > 0) != (slug.mean_marginal_gain > 0)
    )
    return WithinFamilyReview(
        kp=kp,
        slug=slug,
        combined=combined,
        strata_disagree=strata_disagree,
    )


def _within_family_stratum(
    families: Sequence[FamilyNetGains],
    *,
    keying: str,
    bootstrap_samples: int,
    bootstrap_seed: int,
) -> WithinFamilyResult:
    eligible = [
        family
        for family in families
        if family.baseline_gains and family.later_gains
    ]
    excluded = len(families) - len(eligible)
    if not eligible:
        return WithinFamilyResult(
            keying=keying,
            n_families=0,
            mean_marginal_gain=0.0,
            lower_bound_95=0.0,
            passed=False,
            excluded_families=excluded,
        )
    marginals = [
        fmean(family.later_gains) - fmean(family.baseline_gains)
        for family in eligible
    ]
    point = fmean(marginals)
    rng = random.Random(bootstrap_seed)
    n = len(marginals)
    draws = [
        fmean(marginals[rng.randrange(n)] for _ in range(n))
        for _ in range(bootstrap_samples)
    ]
    lower_bound = _one_sided_lower(draws)
    return WithinFamilyResult(
        keying=keying,
        n_families=n,
        mean_marginal_gain=point,
        lower_bound_95=lower_bound,
        passed=lower_bound > 0.0,
        excluded_families=excluded,
    )


def permutation_p_value(
    observed_aulc_contrast: float,
    permutation_contrasts: Sequence[float],
) -> float:
    """One-sided randomization p-value with the standard plus-one correction."""
    if not permutation_contrasts:
        raise ValueError("at least one permutation contrast is required")
    extreme = sum(
        float(value) >= float(observed_aulc_contrast)
        for value in permutation_contrasts
    )
    return (extreme + 1.0) / (len(permutation_contrasts) + 1.0)


def prior_same_family_counts(
    ordered_family_ids: Sequence[str],
) -> tuple[int, ...]:
    seen: dict[str, int] = {}
    result: list[int] = []
    for family_id in ordered_family_ids:
        result.append(seen.get(family_id, 0))
        seen[family_id] = seen.get(family_id, 0) + 1
    return tuple(result)


def _validate_rows(rows: Sequence[EvolutionProbeOutcome]) -> None:
    if not rows:
        raise ValueError("no evolution probe outcomes")
    allowed_probe_sets = {"represented", "unseen"}
    allowed_checkpoints = set(CHECKPOINTS)
    allowed_arms = set(REPRESENTED_ARMS)
    keys: set[tuple[str, int, str, str, int]] = set()
    for item in rows:
        if item.probe_set not in allowed_probe_sets:
            raise ValueError(f"invalid probe_set: {item.probe_set}")
        if item.checkpoint not in allowed_checkpoints:
            raise ValueError(f"invalid checkpoint: {item.checkpoint}")
        if item.arm_id not in allowed_arms:
            raise ValueError(f"invalid arm: {item.arm_id}")
        key = (
            item.recurrent_family_id,
            item.recurrent_variant_index,
            item.checkpoint,
            item.arm_id,
            item.run_seed,
        )
        if key in keys:
            raise ValueError(f"duplicate probe outcome: {key}")
        keys.add(key)
    dimensions: dict[tuple[str, str], set[tuple[str, str, int]]] = {}
    for item in rows:
        dimensions.setdefault(
            (item.probe_set, item.recurrent_family_id), set()
        ).add((item.checkpoint, item.arm_id, item.run_seed))
    for (probe_set, family_id), observed in dimensions.items():
        seeds = {seed for _checkpoint, _arm, seed in observed}
        expected = {
            (checkpoint, arm, seed)
            for checkpoint in CHECKPOINTS
            for arm in REPRESENTED_ARMS
            for seed in seeds
        }
        if observed != expected:
            missing = sorted(expected - observed)
            raise ValueError(
                f"{probe_set}/{family_id}: incomplete paired block; "
                f"missing {missing[:3]}"
            )


def _family_blocks(
    rows: Sequence[EvolutionProbeOutcome],
    probe_set: str,
) -> dict[str, dict[tuple[str, str], float]]:
    grouped: dict[
        str, dict[tuple[str, str], list[float]]
    ] = {}
    for item in rows:
        if item.probe_set != probe_set:
            continue
        grouped.setdefault(item.recurrent_family_id, {}).setdefault(
            (item.arm_id, item.checkpoint), []
        ).append(float(item.recovered))
    return {
        family_id: {
            key: fmean(values) for key, values in family_values.items()
        }
        for family_id, family_values in grouped.items()
    }


def _primary_contrasts(
    blocks: Mapping[str, Mapping[tuple[str, str], float]],
) -> tuple[float, float, float]:
    return _primary_contrasts_from_blocks(list(blocks.values()))


def _primary_contrasts_from_blocks(
    blocks: Sequence[Mapping[tuple[str, str], float]],
) -> tuple[float, float, float]:
    patterned = [
        fmean(block[("patterned", checkpoint)] for block in blocks)
        for checkpoint in CHECKPOINTS
    ]
    no_update = [
        fmean(block[("no_update", checkpoint)] for block in blocks)
        for checkpoint in CHECKPOINTS
    ]
    endpoint = patterned[-1] - patterned[0]
    did = endpoint - (no_update[-1] - no_update[0])
    aulc = normalized_trapezoid(patterned) - normalized_trapezoid(no_update)
    return endpoint, did, aulc


def _safety_contrast(
    blocks: Mapping[str, Mapping[tuple[str, str], float]],
) -> float:
    return _safety_contrast_from_blocks(list(blocks.values()))


def _safety_contrast_from_blocks(
    blocks: Sequence[Mapping[tuple[str, str], float]],
) -> float:
    return fmean(
        block[("patterned", "L3")] - block[("no_update", "L3")]
        for block in blocks
    )


def _mean_block_value(
    blocks: Mapping[str, Mapping[tuple[str, str], float]],
    arm: str,
    checkpoint: str,
) -> float:
    return fmean(block[(arm, checkpoint)] for block in blocks.values())


def _one_sided_lower(values: Sequence[float]) -> float:
    ordered = sorted(float(value) for value in values)
    index = max(0, min(len(ordered) - 1, int(0.05 * len(ordered))))
    return ordered[index]
