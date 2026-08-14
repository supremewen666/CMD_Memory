#!/usr/bin/env python3
"""Zero-call deployment-feedback identifiability audit for GHOST Ecology V2.

The selected-skill feedback passed to GHOST is derived only from typed executor
and deployment-guard telemetry.  Previously materialized recovery gain is read
only by the audit side to test whether that feedback channel can identify useful
repairs; it is never returned by :func:`deployment_feedback`.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import random
from statistics import fmean
from typing import Mapping, Sequence

from cmd_audit.repair.ghost_ecology import content_sha256
from experiments.v4_prequential_runner import V4CandidateOutcome, load_cases


FEEDBACK_SCHEMA_VERSION = "cmd-ghost-skill-conditioned-feedback-v2"
REPORT_SCHEMA_VERSION = "cmd-ghost-ecology-identifiability-v2"
REGISTERED_PROBES: Mapping[str, str] = {
    "verify": "guard_pass_and_no_mutation",
    "abstain": "guard_pass_and_no_immediate_mutation",
    "annotate_conflict": "annotation_commit_observed",
    "replace": "target_mutation_commit_observed",
    "demote": "target_mutation_commit_observed",
    "suppress": "target_mutation_commit_observed",
}


def deployment_feedback(effect: str, outcome: object) -> dict[str, object]:
    """Return the registered immediate probe without reading shadow/gold fields."""
    if effect not in REGISTERED_PROBES:
        raise ValueError(f"unregistered repair effect: {effect}")
    valid = bool(getattr(outcome, "valid"))
    rolled_back = bool(getattr(outcome, "rolled_back"))
    changed = int(getattr(outcome, "changed_item_count"))
    locality = float(getattr(outcome, "locality_cost"))
    if changed < 0 or locality < 0.0 or not math.isfinite(locality):
        raise ValueError("deployment telemetry must be finite and non-negative")
    if effect in {"verify", "abstain"}:
        success = float(valid and not rolled_back and changed == 0)
    else:
        success = float(valid and not rolled_back and changed > 0)
    values: dict[str, object] = {
        "schema_version": FEEDBACK_SCHEMA_VERSION,
        "probe_id": REGISTERED_PROBES[effect],
        "effect": effect,
        "success": success,
        "locality_cost": locality,
        "execution_cost": min(1.0, 0.05 * changed),
        "valid": valid,
        "rolled_back": rolled_back,
        "gold_derived": False,
        "provenance": "typed-executor+deployment-guard-v2",
    }
    return {**values, "feedback_sha256": content_sha256(values)}


def deployment_reward(effect: str, outcome: object) -> float:
    feedback = deployment_feedback(effect, outcome)
    if not feedback["valid"] or feedback["rolled_back"]:
        return -1.0
    return max(
        -1.0,
        min(
            1.0,
            float(feedback["success"])
            - float(feedback["locality_cost"])
            - float(feedback["execution_cost"]),
        ),
    )


def _shadow_utility(outcome: V4CandidateOutcome) -> float:
    if not outcome.valid or outcome.rolled_back:
        return 0.0
    return (
        outcome.recovery_gain
        - outcome.locality_cost
        - 0.05 * outcome.changed_item_count
    )


def _pearson(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or len(left) < 2:
        return 0.0
    left_mean = fmean(left)
    right_mean = fmean(right)
    left_ss = sum((value - left_mean) ** 2 for value in left)
    right_ss = sum((value - right_mean) ** 2 for value in right)
    if left_ss <= 0.0 or right_ss <= 0.0:
        return 0.0
    cross = sum(
        (x - left_mean) * (y - right_mean)
        for x, y in zip(left, right, strict=True)
    )
    return cross / math.sqrt(left_ss * right_ss)


def _lower_one_sided(values: Sequence[float]) -> float:
    ordered = sorted(values)
    return float(ordered[max(0, math.ceil(0.05 * len(ordered)) - 1)])


def audit_identifiability(
    *,
    cases_path: Path,
    output: Path,
    bootstrap_samples: int = 10_000,
    bootstrap_seed: int = 24,
    min_family_correlation: float = 0.2,
    min_bootstrap_lower: float = 0.1,
    min_pairwise_concordance: float = 0.55,
) -> dict[str, object]:
    if bootstrap_samples < 10_000:
        raise ValueError("identifiability audit requires at least 10000 bootstrap draws")
    if output.exists():
        raise ValueError(f"refusing to overwrite identifiability report: {output}")
    cases = load_cases(cases_path)
    direct: list[float] = []
    shadow: list[float] = []
    by_family: dict[str, list[tuple[float, float]]] = defaultdict(list)
    probe_counts: Counter[str] = Counter()
    probe_successes: Counter[str] = Counter()
    valid_count = rollback_count = zero_change_count = one_change_count = 0
    comparable = concordant = 0

    for case in cases:
        outcomes = {row.intent_id: row for row in case.candidate_outcomes}
        case_pairs: list[tuple[float, float]] = []
        for intent in case.intents:
            outcome = outcomes[intent.intent_id]
            feedback = deployment_feedback(intent.effect, outcome)
            left = deployment_reward(intent.effect, outcome)
            right = _shadow_utility(outcome)
            direct.append(left)
            shadow.append(right)
            by_family[case.family_id].append((left, right))
            case_pairs.append((left, right))
            probe = str(feedback["probe_id"])
            probe_counts[probe] += 1
            probe_successes[probe] += int(float(feedback["success"]) > 0.0)
            valid_count += int(outcome.valid)
            rollback_count += int(outcome.rolled_back)
            zero_change_count += int(outcome.changed_item_count == 0)
            one_change_count += int(outcome.changed_item_count == 1)
        for index, current in enumerate(case_pairs):
            for previous in case_pairs[:index]:
                direct_delta = current[0] - previous[0]
                shadow_delta = current[1] - previous[1]
                if direct_delta == 0.0 or shadow_delta == 0.0:
                    continue
                comparable += 1
                concordant += int(direct_delta * shadow_delta > 0.0)

    family_pairs = {
        family: (
            fmean(row[0] for row in values),
            fmean(row[1] for row in values),
        )
        for family, values in by_family.items()
    }
    family_keys = tuple(sorted(family_pairs))
    family_correlation = _pearson(
        [family_pairs[key][0] for key in family_keys],
        [family_pairs[key][1] for key in family_keys],
    )
    rng = random.Random(bootstrap_seed)
    draws = []
    for _ in range(bootstrap_samples):
        chosen = [
            family_keys[rng.randrange(len(family_keys))] for _ in family_keys
        ]
        draws.append(
            _pearson(
                [family_pairs[key][0] for key in chosen],
                [family_pairs[key][1] for key in chosen],
            )
        )
    lower = _lower_one_sided(draws)
    concordance = 0.0 if not comparable else concordant / comparable
    passed = (
        family_correlation >= min_family_correlation
        and lower >= min_bootstrap_lower
        and concordance >= min_pairwise_concordance
    )
    observations = len(direct)
    payload: dict[str, object] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "decision": "PASS" if passed else "BLOCKED_FEEDBACK_NOT_IDENTIFIABLE",
        "model_calls": 0,
        "cases_file_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
        "case_count": len(cases),
        "family_count": len(family_pairs),
        "candidate_observation_count": observations,
        "feedback_schema_version": FEEDBACK_SCHEMA_VERSION,
        "feedback_uses_gold": False,
        "shadow_used_for_audit_only": True,
        "candidate_level_pearson": _pearson(direct, shadow),
        "family_macro_pearson": family_correlation,
        "family_bootstrap_lower_95_one_sided": lower,
        "within_case_pairwise_concordance": concordance,
        "comparable_pair_count": comparable,
        "thresholds": {
            "min_family_correlation": min_family_correlation,
            "min_bootstrap_lower": min_bootstrap_lower,
            "min_pairwise_concordance": min_pairwise_concordance,
        },
        "bootstrap_samples": bootstrap_samples,
        "bootstrap_seed": bootstrap_seed,
        "probe_observation_counts": dict(sorted(probe_counts.items())),
        "probe_success_rates": {
            key: probe_successes[key] / count
            for key, count in sorted(probe_counts.items())
        },
        "telemetry_degeneracy": {
            "valid_rate": valid_count / observations,
            "rollback_rate": rollback_count / observations,
            "zero_change_rate": zero_change_count / observations,
            "exactly_one_change_rate": one_change_count / observations,
            "delayed_regression_observed": False,
            "target_resolution_observed": False,
            "annotation_consumption_observed": False,
        },
        "scope": "development_identifiability_audit_not_router_performance",
    }
    report = {**payload, "report_sha256": content_sha256(payload)}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=24)
    args = parser.parse_args(argv)
    report = audit_identifiability(
        cases_path=args.cases,
        output=args.output,
        bootstrap_samples=args.bootstrap_samples,
        bootstrap_seed=args.seed,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["decision"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "FEEDBACK_SCHEMA_VERSION",
    "REGISTERED_PROBES",
    "REPORT_SCHEMA_VERSION",
    "audit_identifiability",
    "deployment_feedback",
    "deployment_reward",
    "main",
]
