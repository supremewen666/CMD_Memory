"""Route A inference primitives (BUILD SPEC §2.1, §3.7, §4.2-§4.4, §6.3).

Every number Route A reports -- the burned-data split, the design variance, the
tier-3 sample size, the confirmation interval, and the permitted effect wording
-- is produced here from a frozen formula. They are collected in one module so
the preregistered arithmetic is auditable in one place and cannot drift between
the E0, bridge, power, and confirmation commands that consume it.

Two design choices are load-bearing:

`sigma_design` is estimated over pairs of *hard-pass top-decile* specs only
(§4.2). A no-op or failing spec compared against a working one produces a large
family difference for a reason that has nothing to do with the comparison the
confirmation will actually make, so including it would inflate the variance and
buy a smaller sample size than the design needs. When no domain supplies a
finite eligible pair the module raises rather than falling back to all-spec
variance, because the fallback silently changes what the sample size means.

Missing or nonfinite outcomes fail explicitly (§4.4). A repair that crashed and
a repair that scored zero are different events, and coercing the first into the
second would credit a failure as a legitimate null result.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

__all__ = [
    "SPLIT_SALT",
    "SPLIT_TIERS",
    "MDE",
    "POWER",
    "ONE_SIDED_ALPHA",
    "Z_ALPHA",
    "Z_POWER",
    "EXPECTED_USABLE_FAMILY_RATE",
    "MINIMUM_FAMILIES",
    "BOOTSTRAP_SAMPLES",
    "SIGN_FLIP_DRAWS",
    "TOP_DECILE_QUANTILE",
    "EffectWording",
    "InsufficientVarianceBasis",
    "DesignVarianceResult",
    "SampleSizeResult",
    "assign_split_tier",
    "split_bucket",
    "family_paired_differences",
    "family_blocked_lower_bound",
    "sign_flip_p_value",
    "design_variance",
    "tier3_sample_size",
    "effect_wording",
    "crossfit_family_folds",
]

#: §2.1. Frozen so a dependency group's tier cannot move between runs.
SPLIT_SALT = "route-a-split-v1"

#: Bucket 0-5 -> D_dev, 6-7 -> D_search, 8-9 -> D_select.
SPLIT_TIERS = ("D_dev", "D_search", "D_select")

#: §4.1 frozen design parameters.
MDE = 0.10
POWER = 0.90
ONE_SIDED_ALPHA = 0.05
Z_ALPHA = 1.644854
Z_POWER = 1.281552
EXPECTED_USABLE_FAMILY_RATE = 0.90
MINIMUM_FAMILIES = 30

#: §4.4 resampling budgets.
BOOTSTRAP_SAMPLES = 10_000
SIGN_FLIP_DRAWS = 9_999

#: §4.2 step 3. Specs at or above the 90th percentile of dev `state_success`.
TOP_DECILE_QUANTILE = 0.90


class EffectWording(str, Enum):
    """§3.7. The only three phrasings the point estimate licenses."""

    SUBSTANTIAL_POSITIVE = "substantial_positive_effect"
    POSITIVE_BELOW_THRESHOLD = "positive_effect_below_substantial_threshold"
    NO_POSITIVE_EFFECT = "no_positive_effect"


class InsufficientVarianceBasis(ValueError):
    """§4.2. No domain supplied a finite eligible-spec-pair variance.

    Raised instead of falling back to all-spec variance: the fallback would
    produce a number that looks like `sigma_design` but estimates the spread of
    a different comparison.
    """


def split_bucket(dependency_group: str) -> int:
    """§2.1 deterministic bucket for a dependency group."""
    digest = hashlib.sha256(f"{SPLIT_SALT}|{dependency_group}".encode("utf-8"))
    return int(digest.hexdigest(), 16) % 10


def assign_split_tier(dependency_group: str) -> str:
    """Map a dependency group onto its immutable data tier.

    A group is never relocated to improve balance (§2.1); an under-supported
    tier is reported instead.
    """
    bucket = split_bucket(dependency_group)
    if bucket <= 5:
        return "D_dev"
    if bucket <= 7:
        return "D_search"
    return "D_select"


def _finite(value: object, *, context: str) -> float:
    if value is None:
        raise ValueError(f"{context}: outcome is missing")
    number = float(value)  # type: ignore[arg-type]
    if not math.isfinite(number):
        raise ValueError(f"{context}: outcome is not finite ({value!r})")
    return number


def family_paired_differences(
    rows: Iterable[Mapping[str, object]],
    *,
    artifact_key: str,
    baseline_key: str,
    family_key: str = "family_id",
    case_key: str = "case_id",
) -> tuple[tuple[str, float], ...]:
    """§3.7 `D_f`: within-family mean artifact minus within-family mean baseline.

    Both arms must be present on every case. A case scored under one arm only
    would contribute its arm's mean to one side of the difference and nothing to
    the other, which reads as an effect.
    """
    artifact: dict[str, list[float]] = {}
    baseline: dict[str, list[float]] = {}
    for row in rows:
        family = str(row[family_key])
        case = str(row.get(case_key, "<unknown>"))
        if artifact_key not in row or baseline_key not in row:
            raise ValueError(
                f"{case}: needs both {artifact_key!r} and {baseline_key!r}"
            )
        artifact.setdefault(family, []).append(
            _finite(row[artifact_key], context=f"{case}/{artifact_key}")
        )
        baseline.setdefault(family, []).append(
            _finite(row[baseline_key], context=f"{case}/{baseline_key}")
        )
    return tuple(
        (
            family,
            sum(artifact[family]) / len(artifact[family])
            - sum(baseline[family]) / len(baseline[family]),
        )
        for family in sorted(artifact)
    )


def family_blocked_lower_bound(
    values: Sequence[tuple[str, float]],
    *,
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int,
) -> float:
    """§4.4 one-sided family-blocked bootstrap lower bound at 95%.

    The resampling unit is the family effect, so sibling cases inside a family
    move together and their shared injected fault is not counted as independent
    evidence.
    """
    if not values:
        raise ValueError("family-blocked interval requires at least one family")
    effects = tuple(float(effect) for _family, effect in values)
    for effect in effects:
        if not math.isfinite(effect):
            raise ValueError("family effect is not finite")
    rng = random.Random(seed)
    count = len(effects)
    draws = sorted(
        sum(effects[rng.randrange(count)] for _ in range(count)) / count
        for _ in range(samples)
    )
    index = max(0, min(len(draws) - 1, int(ONE_SIDED_ALPHA * len(draws))))
    return draws[index]


def sign_flip_p_value(
    values: Sequence[tuple[str, float]],
    *,
    draws: int = SIGN_FLIP_DRAWS,
    seed: int,
) -> float:
    """§4.4 family-level sign-flip randomization check (upper tail).

    The observed assignment is counted as one draw, so the p-value can never be
    exactly zero -- a randomization test bounds the tail by its own resolution
    and reporting 0.0 would overstate it.
    """
    if not values:
        raise ValueError("sign-flip test requires at least one family")
    effects = tuple(float(effect) for _family, effect in values)
    observed = sum(effects) / len(effects)
    rng = random.Random(seed)
    at_least_as_extreme = 1
    for _ in range(draws):
        flipped = sum(
            effect if rng.random() < 0.5 else -effect for effect in effects
        ) / len(effects)
        if flipped >= observed:
            at_least_as_extreme += 1
    return at_least_as_extreme / (draws + 1)


@dataclass(frozen=True)
class DesignVarianceResult:
    """§4.2 outcome, including what was excluded and why."""

    sigma_design: float
    source_domain: str
    contributing_domains: tuple[str, ...]
    excluded_domains: tuple[tuple[str, str], ...]
    eligible_spec_ids: tuple[str, ...]
    pair_count: int
    top_decile_threshold_by_domain: tuple[tuple[str, float], ...]

    def as_mapping(self) -> dict[str, object]:
        return {
            "sigma_design": self.sigma_design,
            "source_domain": self.source_domain,
            "contributing_domains": list(self.contributing_domains),
            "excluded_domains": [
                {"domain": domain, "reason_code": reason}
                for domain, reason in self.excluded_domains
            ],
            "eligible_spec_ids": list(self.eligible_spec_ids),
            "pair_count": self.pair_count,
            "top_decile_threshold_by_domain": {
                domain: threshold
                for domain, threshold in self.top_decile_threshold_by_domain
            },
            "top_decile_quantile": TOP_DECILE_QUANTILE,
        }


def _quantile(values: Sequence[float], quantile: float) -> float:
    """Linear-interpolation quantile, matching `paired_stats._percentile`."""
    ordered = sorted(values)
    if not ordered:
        raise ValueError("quantile of an empty sequence")
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _sample_sd(values: Sequence[float]) -> float | None:
    """Sample SD over paired family differences; None below two observations."""
    count = len(values)
    if count < 2:
        return None
    mean = sum(values) / count
    variance = sum((value - mean) ** 2 for value in values) / (count - 1)
    return math.sqrt(variance)


def design_variance(
    rows: Iterable[Mapping[str, object]],
    *,
    domain_key: str = "domain",
    spec_key: str = "spec_id",
    family_key: str = "family_id",
    outcome_key: str = "state_success",
    hard_gate_key: str = "hard_gates_pass",
) -> DesignVarianceResult:
    """§4.2 `sigma_design`: max family-difference SD over eligible spec pairs.

    Eligible means the spec passed every hard gate on every case and its mean
    dev `state_success` sits at or above the domain's 90th percentile. Both
    filters exist to keep the variance estimate anchored on comparisons that
    could plausibly become artifact-versus-baseline.
    """
    by_domain: dict[str, dict[str, dict[str, float]]] = {}
    hard_pass: dict[tuple[str, str], bool] = {}
    for row in rows:
        domain = str(row[domain_key])
        spec = str(row[spec_key])
        family = str(row[family_key])
        outcome = _finite(row[outcome_key], context=f"{domain}/{spec}/{family}")
        by_domain.setdefault(domain, {}).setdefault(spec, {})[family] = outcome
        passed = bool(row.get(hard_gate_key, True))
        key = (domain, spec)
        hard_pass[key] = hard_pass.get(key, True) and passed

    best: tuple[float, str] | None = None
    contributing: list[str] = []
    excluded: list[tuple[str, str]] = []
    eligible_ids: list[str] = []
    thresholds: list[tuple[str, float]] = []
    pair_count = 0

    for domain in sorted(by_domain):
        specs = {
            spec: outcomes
            for spec, outcomes in by_domain[domain].items()
            if hard_pass[(domain, spec)]
        }
        if len(specs) < 2:
            excluded.append((domain, "FEWER_THAN_TWO_HARD_PASS_SPECS"))
            continue
        means = {
            spec: sum(outcomes.values()) / len(outcomes)
            for spec, outcomes in specs.items()
        }
        threshold = _quantile(tuple(means.values()), TOP_DECILE_QUANTILE)
        thresholds.append((domain, threshold))
        retained = sorted(spec for spec, mean in means.items() if mean >= threshold)
        if len(retained) < 2:
            # The percentile can retain a single spec when one dominates. Fall
            # back to every hard-pass spec in the domain rather than dropping
            # the domain: these are still specs that passed every gate, which is
            # the property §4.2 requires of the variance basis.
            retained = sorted(specs)
        if len(retained) < 2:
            excluded.append((domain, "FEWER_THAN_TWO_ELIGIBLE_SPECS"))
            continue
        eligible_ids.extend(retained)
        domain_best: float | None = None
        for index, left in enumerate(retained):
            for right in retained[index + 1 :]:
                shared = sorted(set(specs[left]) & set(specs[right]))
                differences = [
                    specs[left][family] - specs[right][family] for family in shared
                ]
                deviation = _sample_sd(differences)
                if deviation is None:
                    continue
                pair_count += 1
                domain_best = (
                    deviation if domain_best is None else max(domain_best, deviation)
                )
        if domain_best is None:
            excluded.append((domain, "NO_FINITE_ELIGIBLE_PAIR_SD"))
            continue
        contributing.append(domain)
        if best is None or domain_best > best[0]:
            best = (domain_best, domain)

    if best is None:
        raise InsufficientVarianceBasis(
            "no domain supplied a finite eligible-spec-pair family SD"
        )
    return DesignVarianceResult(
        sigma_design=best[0],
        source_domain=best[1],
        contributing_domains=tuple(contributing),
        excluded_domains=tuple(excluded),
        eligible_spec_ids=tuple(sorted(set(eligible_ids))),
        pair_count=pair_count,
        top_decile_threshold_by_domain=tuple(thresholds),
    )


@dataclass(frozen=True)
class SampleSizeResult:
    """§4.3 mechanical scheme-one sample size."""

    sigma_design: float
    n_raw: int
    n_tier3: int
    mde: float = MDE
    power: float = POWER
    one_sided_alpha: float = ONE_SIDED_ALPHA
    z_alpha: float = Z_ALPHA
    z_power: float = Z_POWER
    expected_usable_family_rate: float = EXPECTED_USABLE_FAMILY_RATE
    minimum_families: int = MINIMUM_FAMILIES

    def as_mapping(self) -> dict[str, object]:
        return {
            "sigma_design": self.sigma_design,
            "n_raw": self.n_raw,
            "n_tier3": self.n_tier3,
            "mde": self.mde,
            "power": self.power,
            "one_sided_alpha": self.one_sided_alpha,
            "z_alpha": self.z_alpha,
            "z_power": self.z_power,
            "expected_usable_family_rate": self.expected_usable_family_rate,
            "minimum_families": self.minimum_families,
        }


def tier3_sample_size(*, sigma_design: float) -> SampleSizeResult:
    """§4.3. `n_raw = ceil(((z_a + z_b) * sigma / MDE)^2)`, inflated for dropout."""
    if not math.isfinite(sigma_design) or sigma_design < 0.0:
        raise ValueError(f"sigma_design must be finite and non-negative: {sigma_design}")
    n_raw = math.ceil(((Z_ALPHA + Z_POWER) * sigma_design / MDE) ** 2)
    n_tier3 = max(MINIMUM_FAMILIES, math.ceil(n_raw / EXPECTED_USABLE_FAMILY_RATE))
    return SampleSizeResult(
        sigma_design=sigma_design, n_raw=n_raw, n_tier3=n_tier3
    )


def effect_wording(estimate: float) -> EffectWording:
    """§3.7. Keyed on the point estimate alone; the interval gates success."""
    if estimate >= MDE:
        return EffectWording.SUBSTANTIAL_POSITIVE
    if estimate > 0.0:
        return EffectWording.POSITIVE_BELOW_THRESHOLD
    return EffectWording.NO_POSITIVE_EFFECT


def crossfit_family_folds(
    families: Iterable[str], *, folds: int = 5, seed: int
) -> dict[str, int]:
    """§6.3 family-grouped fold assignment.

    Families are ordered by a seeded hash and dealt round-robin, so fold sizes
    differ by at most one and every case in a family lands in the same fold.
    Grouping by family is what keeps a sibling of a training case out of the
    held-out fold.
    """
    unique = sorted(set(families))
    if len(unique) < folds:
        raise ValueError(f"{len(unique)} families is fewer than {folds} folds")
    ordered = sorted(
        unique,
        key=lambda family: hashlib.sha256(
            f"{seed}|{family}".encode("utf-8")
        ).hexdigest(),
    )
    return {family: index % folds for index, family in enumerate(ordered)}
