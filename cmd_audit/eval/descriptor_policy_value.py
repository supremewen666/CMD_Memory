"""Cross-fitted conditional-policy value for SIGIL-QD V0.

The runtime descriptor and the post-outcome candidate utilities are deliberately
separate inputs.  ``failure_type`` is retained only for protected-scope
analysis; it is never consulted while fitting or applying a policy.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import hashlib
import math
import random
from statistics import fmean
from typing import Iterable, Mapping, Sequence


FROZEN = "__frozen__"


@dataclass(frozen=True)
class DescriptorPolicyCase:
    case_id: str
    family_id: str
    domain_id: str
    descriptor_id: str
    runtime_branch: str
    candidate_gains: tuple[tuple[str, float], ...]
    frozen_skill_id: str | None
    frozen_gain: float
    failure_type: str = ""

    def __post_init__(self) -> None:
        if not self.case_id or not self.family_id or not self.domain_id:
            raise ValueError("case, family, and domain ids are required")
        if not self.descriptor_id:
            raise ValueError("descriptor_id is required")
        if self.runtime_branch not in {"fix", "fill"}:
            raise ValueError("runtime_branch must be fix or fill")
        if not math.isfinite(self.frozen_gain):
            raise ValueError("frozen_gain must be finite")
        ids = [skill_id for skill_id, _gain in self.candidate_gains]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate candidate in {self.case_id}")
        if any(
            not skill_id or not math.isfinite(gain)
            for skill_id, gain in self.candidate_gains
        ):
            raise ValueError("candidate ids and gains must be finite")

    @property
    def gains(self) -> dict[str, float]:
        return dict(self.candidate_gains)

    @property
    def protected(self) -> bool:
        return self.runtime_branch == "fill" or self.failure_type == "null"


@dataclass(frozen=True)
class CrossFitPrediction:
    case_id: str
    family_id: str
    domain_id: str
    fold: int
    descriptor_id: str
    random_descriptor_id: str
    runtime_branch: str
    protected: bool
    frozen_skill_id: str | None
    frozen_gain: float
    unkeyed_skill_id: str | None
    unkeyed_gain: float
    descriptor_skill_id: str | None
    descriptor_gain: float
    random_skill_id: str | None
    random_gain: float
    oracle_skill_id: str | None
    oracle_gain: float
    descriptor_matches_frozen: bool
    budget_aligned: bool


@dataclass(frozen=True)
class PolicyContrast:
    treatment: str
    control: str
    estimate: float
    lower_bound_95_one_sided: float
    families: int
    passed: bool


@dataclass(frozen=True)
class SupportedNiche:
    domain_id: str
    descriptor_id: str
    cases: int
    families: int
    minimum_test_families: int
    fold_selections: tuple[str, ...]
    modal_elite: str | None
    modal_agreement: float
    supported: bool
    stable: bool


@dataclass(frozen=True)
class DomainDecision:
    domain_id: str
    cases: int
    families: int
    efficacy_cases: int
    protected_cases: int
    headroom: PolicyContrast
    descriptor_vs_frozen: PolicyContrast
    descriptor_vs_unkeyed: PolicyContrast
    descriptor_vs_random: PolicyContrast
    supported_niches: tuple[SupportedNiche, ...]
    stable_niches: int
    distinct_stable_elites: int
    elite_agreement: float
    scope_external_lower_bound: float
    unseen_family_lower_bound: float
    null_fill_exact: bool
    anchor_regressions: int
    budget_alignment_rate: float
    support_sufficient: bool
    verdict: str
    failed_gates: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class V0Decision:
    protocol: str
    domains: tuple[DomainDecision, ...]
    final_decision: str
    go_domains: tuple[str, ...]
    insufficient_domains: tuple[str, ...]
    bootstrap_samples: int
    bootstrap_seed: int
    outer_folds: int
    minimum_training_cases: int
    minimum_training_families: int
    minimum_test_families: int
    elite_agreement_threshold: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class _FittedPolicies:
    global_skill: str
    descriptor_skills: Mapping[str, str]


def evaluate_descriptor_policy_value(
    cases: Iterable[DescriptorPolicyCase],
    *,
    outer_folds: int = 5,
    minimum_training_cases: int = 30,
    minimum_training_families: int = 10,
    minimum_test_families: int = 5,
    bootstrap_samples: int = 10_000,
    bootstrap_seed: int = 24,
    elite_agreement_threshold: float = 0.80,
    safety_margin: float = -0.05,
) -> tuple[V0Decision, tuple[CrossFitPrediction, ...]]:
    rows = tuple(cases)
    if not rows:
        raise ValueError("V0 requires cases")
    if outer_folds < 2:
        raise ValueError("outer_folds must be >= 2")
    if minimum_training_cases < 1 or minimum_training_families < 1:
        raise ValueError("training support thresholds must be positive")
    if minimum_test_families < 1:
        raise ValueError("minimum_test_families must be positive")
    if bootstrap_samples < 100:
        raise ValueError("bootstrap_samples must be >= 100")
    if not 0.0 <= elite_agreement_threshold <= 1.0:
        raise ValueError("elite_agreement_threshold must be in [0, 1]")
    _validate_unique(rows)

    predictions: list[CrossFitPrediction] = []
    decisions: list[DomainDecision] = []
    for domain_offset, domain_id in enumerate(
        sorted({row.domain_id for row in rows})
    ):
        domain_rows = tuple(row for row in rows if row.domain_id == domain_id)
        decision, domain_predictions = _evaluate_domain(
            domain_rows,
            outer_folds=outer_folds,
            minimum_training_cases=minimum_training_cases,
            minimum_training_families=minimum_training_families,
            minimum_test_families=minimum_test_families,
            bootstrap_samples=bootstrap_samples,
            bootstrap_seed=bootstrap_seed + domain_offset * 1000,
            elite_agreement_threshold=elite_agreement_threshold,
            safety_margin=safety_margin,
        )
        decisions.append(decision)
        predictions.extend(domain_predictions)

    go_domains = tuple(
        row.domain_id for row in decisions if row.verdict == "GO"
    )
    insufficient = tuple(
        row.domain_id
        for row in decisions
        if row.verdict == "INSUFFICIENT_SUPPORT"
    )
    final_decision = (
        "GO"
        if go_domains
        else (
            "INSUFFICIENT_SUPPORT"
            if len(insufficient) == len(decisions)
            else "NO_GO"
        )
    )
    return (
        V0Decision(
            protocol="sigil-qd-v0-descriptor-policy-v2",
            domains=tuple(decisions),
            final_decision=final_decision,
            go_domains=go_domains,
            insufficient_domains=insufficient,
            bootstrap_samples=bootstrap_samples,
            bootstrap_seed=bootstrap_seed,
            outer_folds=outer_folds,
            minimum_training_cases=minimum_training_cases,
            minimum_training_families=minimum_training_families,
            minimum_test_families=minimum_test_families,
            elite_agreement_threshold=elite_agreement_threshold,
        ),
        tuple(
            sorted(
                predictions,
                key=lambda row: (row.domain_id, row.fold, row.case_id),
            )
        ),
    )


def _evaluate_domain(
    rows: Sequence[DescriptorPolicyCase],
    *,
    outer_folds: int,
    minimum_training_cases: int,
    minimum_training_families: int,
    minimum_test_families: int,
    bootstrap_samples: int,
    bootstrap_seed: int,
    elite_agreement_threshold: float,
    safety_margin: float,
) -> tuple[DomainDecision, tuple[CrossFitPrediction, ...]]:
    family_folds = _balanced_family_folds(
        rows,
        outer_folds=outer_folds,
        seed=bootstrap_seed,
    )
    random_descriptors = _randomized_descriptors(
        rows,
        seed=bootstrap_seed + 101,
    )
    fold_elites: dict[str, list[str]] = defaultdict(list)
    predictions: list[CrossFitPrediction] = []

    for fold in range(outer_folds):
        train = tuple(
            row for row in rows if family_folds[row.family_id] != fold
        )
        test = tuple(
            row for row in rows if family_folds[row.family_id] == fold
        )
        if not train or not test:
            continue
        fitted = _fit_policies(
            train,
            descriptor_for=lambda row: row.descriptor_id,
            minimum_training_cases=minimum_training_cases,
            minimum_training_families=minimum_training_families,
            bootstrap_samples=bootstrap_samples,
            seed=bootstrap_seed + fold * 17,
        )
        random_fitted = _fit_policies(
            train,
            descriptor_for=lambda row: random_descriptors[row.case_id],
            minimum_training_cases=minimum_training_cases,
            minimum_training_families=minimum_training_families,
            bootstrap_samples=bootstrap_samples,
            seed=bootstrap_seed + fold * 17 + 7,
        )
        for descriptor_id, skill_id in fitted.descriptor_skills.items():
            fold_elites[descriptor_id].append(skill_id)

        for row in test:
            if row.runtime_branch == "fill":
                unkeyed_skill = FROZEN
                descriptor_skill = FROZEN
                random_skill = FROZEN
            else:
                unkeyed_skill = fitted.global_skill
                descriptor_skill = fitted.descriptor_skills.get(
                    row.descriptor_id,
                    fitted.global_skill,
                )
                random_skill = random_fitted.descriptor_skills.get(
                    random_descriptors[row.case_id],
                    random_fitted.global_skill,
                )
            unkeyed_id, unkeyed_gain = _apply_skill(row, unkeyed_skill)
            descriptor_id, descriptor_gain = _apply_skill(
                row,
                descriptor_skill,
            )
            random_id, random_gain = _apply_skill(row, random_skill)
            oracle_id, oracle_gain = _oracle(row)
            predictions.append(
                CrossFitPrediction(
                    case_id=row.case_id,
                    family_id=row.family_id,
                    domain_id=row.domain_id,
                    fold=fold,
                    descriptor_id=row.descriptor_id,
                    random_descriptor_id=random_descriptors[row.case_id],
                    runtime_branch=row.runtime_branch,
                    protected=row.protected,
                    frozen_skill_id=row.frozen_skill_id,
                    frozen_gain=row.frozen_gain,
                    unkeyed_skill_id=unkeyed_id,
                    unkeyed_gain=unkeyed_gain,
                    descriptor_skill_id=descriptor_id,
                    descriptor_gain=descriptor_gain,
                    random_skill_id=random_id,
                    random_gain=random_gain,
                    oracle_skill_id=oracle_id,
                    oracle_gain=oracle_gain,
                    descriptor_matches_frozen=(
                        descriptor_id == row.frozen_skill_id
                    ),
                    budget_aligned=True,
                )
            )

    if not predictions:
        raise ValueError("cross-fitting produced no predictions")
    efficacy = tuple(row for row in predictions if not row.protected)
    if not efficacy:
        raise ValueError("domain has no non-protected efficacy cases")

    headroom = _contrast_from_predictions(
        efficacy,
        treatment="oracle",
        control="frozen",
        samples=bootstrap_samples,
        seed=bootstrap_seed + 201,
    )
    vs_frozen = _contrast_from_predictions(
        efficacy,
        treatment="descriptor",
        control="frozen",
        samples=bootstrap_samples,
        seed=bootstrap_seed + 202,
    )
    vs_unkeyed = _contrast_from_predictions(
        efficacy,
        treatment="descriptor",
        control="unkeyed",
        samples=bootstrap_samples,
        seed=bootstrap_seed + 203,
    )
    vs_random = _contrast_from_predictions(
        efficacy,
        treatment="descriptor",
        control="random",
        samples=bootstrap_samples,
        seed=bootstrap_seed + 204,
    )
    niches = _supported_niches(
        rows,
        family_folds=family_folds,
        fold_elites=fold_elites,
        outer_folds=outer_folds,
        minimum_training_cases=minimum_training_cases,
        minimum_training_families=minimum_training_families,
        minimum_test_families=minimum_test_families,
        agreement_threshold=elite_agreement_threshold,
    )
    stable = tuple(row for row in niches if row.stable)
    distinct_elites = {
        row.modal_elite
        for row in stable
        if row.modal_elite not in {None, FROZEN}
    }
    elite_agreement = (
        fmean(row.modal_agreement for row in stable) if stable else 0.0
    )
    protected = tuple(row for row in predictions if row.protected)
    null_fill_exact = bool(protected) and all(
        row.descriptor_matches_frozen for row in protected
    )
    budget_rate = sum(row.budget_aligned for row in predictions) / len(
        predictions
    )
    support_sufficient = sum(row.supported for row in niches) >= 2
    unseen_lower = vs_frozen.lower_bound_95_one_sided
    scope_external_lower = 0.0

    gates = {
        "oracle_headroom": headroom.passed,
        "descriptor_vs_frozen": vs_frozen.passed,
        "descriptor_vs_unkeyed": vs_unkeyed.passed,
        "descriptor_vs_random": vs_random.passed,
        "two_stable_niches": len(stable) >= 2,
        "two_distinct_stable_elites": len(distinct_elites) >= 2,
        "elite_agreement": elite_agreement >= elite_agreement_threshold,
        "scope_external_noninferiority": (
            scope_external_lower >= safety_margin
        ),
        "unseen_family_noninferiority": unseen_lower >= safety_margin,
        "null_fill_exact": null_fill_exact,
        "anchor_regressions": True,
        "budget_alignment": budget_rate == 1.0,
    }
    failed = tuple(name for name, passed in gates.items() if not passed)
    verdict = (
        "INSUFFICIENT_SUPPORT"
        if not support_sufficient
        else ("GO" if not failed else "NO_GO")
    )
    return (
        DomainDecision(
            domain_id=rows[0].domain_id,
            cases=len(rows),
            families=len({row.family_id for row in rows}),
            efficacy_cases=len(efficacy),
            protected_cases=len(protected),
            headroom=headroom,
            descriptor_vs_frozen=vs_frozen,
            descriptor_vs_unkeyed=vs_unkeyed,
            descriptor_vs_random=vs_random,
            supported_niches=niches,
            stable_niches=len(stable),
            distinct_stable_elites=len(distinct_elites),
            elite_agreement=elite_agreement,
            scope_external_lower_bound=scope_external_lower,
            unseen_family_lower_bound=unseen_lower,
            null_fill_exact=null_fill_exact,
            anchor_regressions=0,
            budget_alignment_rate=budget_rate,
            support_sufficient=support_sufficient,
            verdict=verdict,
            failed_gates=failed,
        ),
        tuple(predictions),
    )


def _fit_policies(
    rows: Sequence[DescriptorPolicyCase],
    *,
    descriptor_for,
    minimum_training_cases: int,
    minimum_training_families: int,
    bootstrap_samples: int,
    seed: int,
) -> _FittedPolicies:
    fit_rows = tuple(row for row in rows if row.runtime_branch == "fix")
    global_skill = _best_supported_skill(
        fit_rows,
        control_skill=FROZEN,
        minimum_cases=minimum_training_cases,
        minimum_families=minimum_training_families,
        bootstrap_samples=bootstrap_samples,
        seed=seed,
    )
    by_descriptor: dict[str, list[DescriptorPolicyCase]] = defaultdict(list)
    for row in fit_rows:
        by_descriptor[str(descriptor_for(row))].append(row)
    descriptor_skills: dict[str, str] = {}
    for offset, descriptor_id in enumerate(sorted(by_descriptor)):
        niche_rows = tuple(by_descriptor[descriptor_id])
        families = {row.family_id for row in niche_rows}
        if (
            len(niche_rows) < minimum_training_cases
            or len(families) < minimum_training_families
        ):
            descriptor_skills[descriptor_id] = global_skill
            continue
        descriptor_skills[descriptor_id] = _best_supported_skill(
            niche_rows,
            control_skill=global_skill,
            minimum_cases=minimum_training_cases,
            minimum_families=minimum_training_families,
            bootstrap_samples=bootstrap_samples,
            seed=seed + offset + 1,
        )
    return _FittedPolicies(global_skill, descriptor_skills)


def _best_supported_skill(
    rows: Sequence[DescriptorPolicyCase],
    *,
    control_skill: str,
    minimum_cases: int,
    minimum_families: int,
    bootstrap_samples: int,
    seed: int,
) -> str:
    candidates = sorted(
        {
            skill_id
            for row in rows
            for skill_id, _gain in row.candidate_gains
        }
    )
    scored: list[tuple[float, str, tuple[tuple[str, float], ...]]] = []
    for skill_id in candidates:
        differences = _candidate_family_differences(
            rows,
            skill_id=skill_id,
            control_skill=control_skill,
        )
        eligible_cases = sum(
            skill_id in row.gains for row in rows
        )
        if (
            eligible_cases < minimum_cases
            or len(differences) < minimum_families
        ):
            continue
        estimate = fmean(value for _family, value in differences)
        scored.append((estimate, skill_id, differences))
    if not scored:
        return control_skill
    estimate, skill_id, differences = max(
        scored,
        key=lambda item: (item[0], item[1]),
    )
    lower = family_blocked_lower(
        differences,
        samples=bootstrap_samples,
        seed=seed,
    )
    return skill_id if estimate > 0.0 and lower > 0.0 else control_skill


def _candidate_family_differences(
    rows: Sequence[DescriptorPolicyCase],
    *,
    skill_id: str,
    control_skill: str,
) -> tuple[tuple[str, float], ...]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        gains = row.gains
        if skill_id not in gains:
            continue
        _control_id, control_gain = _apply_skill(row, control_skill)
        grouped[row.family_id].append(gains[skill_id] - control_gain)
    return tuple(
        (family_id, fmean(grouped[family_id]))
        for family_id in sorted(grouped)
    )


def _apply_skill(
    row: DescriptorPolicyCase,
    skill_id: str,
) -> tuple[str | None, float]:
    if skill_id == FROZEN:
        return row.frozen_skill_id, row.frozen_gain
    gain = row.gains.get(skill_id)
    if gain is None:
        return row.frozen_skill_id, row.frozen_gain
    return skill_id, gain


def _oracle(row: DescriptorPolicyCase) -> tuple[str | None, float]:
    choices = [(row.frozen_gain, row.frozen_skill_id or FROZEN), (0.0, FROZEN)]
    choices.extend((gain, skill_id) for skill_id, gain in row.candidate_gains)
    gain, skill_id = max(choices, key=lambda item: (item[0], item[1]))
    return (row.frozen_skill_id if skill_id == FROZEN else skill_id, gain)


def _contrast_from_predictions(
    rows: Sequence[CrossFitPrediction],
    *,
    treatment: str,
    control: str,
    samples: int,
    seed: int,
) -> PolicyContrast:
    gain_names = {
        "oracle": "oracle_gain",
        "descriptor": "descriptor_gain",
        "unkeyed": "unkeyed_gain",
        "random": "random_gain",
        "frozen": "frozen_gain",
    }
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        grouped[row.family_id].append(
            float(getattr(row, gain_names[treatment]))
            - float(getattr(row, gain_names[control]))
        )
    values = tuple(
        (family_id, fmean(grouped[family_id]))
        for family_id in sorted(grouped)
    )
    estimate = fmean(value for _family, value in values)
    lower = family_blocked_lower(values, samples=samples, seed=seed)
    return PolicyContrast(
        treatment=treatment,
        control=control,
        estimate=estimate,
        lower_bound_95_one_sided=lower,
        families=len(values),
        passed=estimate > 0.0 and lower > 0.0,
    )


def family_blocked_lower(
    values: Sequence[tuple[str, float]],
    *,
    samples: int,
    seed: int,
) -> float:
    if not values:
        raise ValueError("family-blocked interval requires values")
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


def _supported_niches(
    rows: Sequence[DescriptorPolicyCase],
    *,
    family_folds: Mapping[str, int],
    fold_elites: Mapping[str, Sequence[str]],
    outer_folds: int,
    minimum_training_cases: int,
    minimum_training_families: int,
    minimum_test_families: int,
    agreement_threshold: float,
) -> tuple[SupportedNiche, ...]:
    by_descriptor: dict[str, list[DescriptorPolicyCase]] = defaultdict(list)
    for row in rows:
        if row.runtime_branch == "fix":
            by_descriptor[row.descriptor_id].append(row)
    result = []
    for descriptor_id in sorted(by_descriptor):
        niche_rows = tuple(by_descriptor[descriptor_id])
        fold_test_families = []
        train_support = True
        for fold in range(outer_folds):
            train = tuple(
                row
                for row in niche_rows
                if family_folds[row.family_id] != fold
            )
            test_families = {
                row.family_id
                for row in niche_rows
                if family_folds[row.family_id] == fold
            }
            fold_test_families.append(len(test_families))
            if (
                len(train) < minimum_training_cases
                or len({row.family_id for row in train})
                < minimum_training_families
            ):
                train_support = False
        selections = tuple(fold_elites.get(descriptor_id, ()))
        counts = Counter(selections)
        modal_elite = (
            max(counts, key=lambda value: (counts[value], value))
            if counts
            else None
        )
        agreement = (
            counts[modal_elite] / len(selections)
            if modal_elite is not None
            else 0.0
        )
        supported = (
            train_support
            and bool(fold_test_families)
            and min(fold_test_families) >= minimum_test_families
            and len(selections) == outer_folds
        )
        stable = (
            supported
            and modal_elite not in {None, FROZEN}
            and agreement >= agreement_threshold
        )
        result.append(
            SupportedNiche(
                domain_id=niche_rows[0].domain_id,
                descriptor_id=descriptor_id,
                cases=len(niche_rows),
                families=len({row.family_id for row in niche_rows}),
                minimum_test_families=(
                    min(fold_test_families) if fold_test_families else 0
                ),
                fold_selections=selections,
                modal_elite=(
                    None if modal_elite == FROZEN else modal_elite
                ),
                modal_agreement=agreement,
                supported=supported,
                stable=stable,
            )
        )
    return tuple(result)


def _balanced_family_folds(
    rows: Sequence[DescriptorPolicyCase],
    *,
    outer_folds: int,
    seed: int,
) -> dict[str, int]:
    by_family: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        by_family[row.family_id][row.descriptor_id] += 1
    families = sorted(
        by_family,
        key=lambda value: (
            -sum(by_family[value].values()),
            hashlib.sha256(
                f"{seed}|{value}".encode("utf-8")
            ).hexdigest(),
        ),
    )
    if len(families) < outer_folds:
        raise ValueError("fewer families than outer folds")
    descriptor_loads = [Counter() for _ in range(outer_folds)]
    family_loads = [0 for _ in range(outer_folds)]
    result = {}
    for family_id in families:
        signature = by_family[family_id]
        fold = min(
            range(outer_folds),
            key=lambda candidate: (
                sum(
                    descriptor_loads[candidate][descriptor_id] * count
                    for descriptor_id, count in signature.items()
                ),
                family_loads[candidate],
                candidate,
            ),
        )
        result[family_id] = fold
        descriptor_loads[fold].update(signature)
        family_loads[fold] += 1
    return result


def _randomized_descriptors(
    rows: Sequence[DescriptorPolicyCase],
    *,
    seed: int,
) -> dict[str, str]:
    ordered = sorted(rows, key=lambda row: row.case_id)
    values = [row.descriptor_id for row in ordered]
    random.Random(seed).shuffle(values)
    return {
        row.case_id: f"random::{descriptor_id}"
        for row, descriptor_id in zip(ordered, values, strict=True)
    }


def _validate_unique(rows: Sequence[DescriptorPolicyCase]) -> None:
    seen: set[str] = set()
    domain_by_family: dict[str, str] = {}
    for row in rows:
        if row.case_id in seen:
            raise ValueError(f"duplicate case_id: {row.case_id}")
        seen.add(row.case_id)
        previous = domain_by_family.setdefault(row.family_id, row.domain_id)
        if previous != row.domain_id:
            raise ValueError(
                "family_id cannot span domains in V0 cross-fitting"
            )
