#!/usr/bin/env python3
"""Fit on frozen ghost_dev and audit on family-disjoint ghost_cal, zero-call."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import random
from statistics import fmean
from typing import Mapping, Sequence

from cmd_audit.repair.deployment_feedback_evaluator import (
    EvaluatorTrainingRow,
    FrozenDeploymentEvaluator,
    pre_action_features,
)
from cmd_audit.repair.ghost_ecology import content_sha256
from experiments.v4_prequential_runner import V4CandidateOutcome, load_cases


REPORT_SCHEMA_VERSION = "cmd-ghost-deployment-evaluator-identifiability-v1"


def _pearson(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or len(left) < 2:
        return 0.0
    left_mean, right_mean = fmean(left), fmean(right)
    left_ss = sum((row - left_mean) ** 2 for row in left)
    right_ss = sum((row - right_mean) ** 2 for row in right)
    if left_ss == 0.0 or right_ss == 0.0:
        return 0.0
    return sum(
        (x - left_mean) * (y - right_mean)
        for x, y in zip(left, right, strict=True)
    ) / math.sqrt(left_ss * right_ss)


def _shadow_utility(outcome: V4CandidateOutcome) -> float:
    if not outcome.valid or outcome.rolled_back:
        return 0.0
    return outcome.recovery_gain - outcome.locality_cost - 0.05 * outcome.changed_item_count


def _assignments(protocol: Mapping[str, object]) -> dict[str, str]:
    rows = protocol.get("assignments")
    if not isinstance(rows, list):
        raise ValueError("protocol assignments are missing")
    assignments: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, Mapping) or set(row) < {"case_id", "partition"}:
            raise ValueError("protocol assignment is invalid")
        case_id, partition = row["case_id"], row["partition"]
        if not isinstance(case_id, str) or partition not in {"ghost_dev", "ghost_cal"}:
            raise ValueError("evaluator audit accepts only ghost_dev/ghost_cal")
        if case_id in assignments:
            raise ValueError("protocol repeats a case assignment")
        assignments[case_id] = str(partition)
    return assignments


def run_audit(
    *,
    cases_path: Path,
    protocol_path: Path,
    evaluator_output: Path,
    report_output: Path,
    bootstrap_samples: int = 10_000,
    bootstrap_seed: int = 24,
) -> dict[str, object]:
    if bootstrap_samples < 10_000:
        raise ValueError("evaluator audit requires at least 10000 bootstrap samples")
    if evaluator_output.exists() or report_output.exists():
        raise ValueError("refusing to overwrite evaluator audit artifacts")
    cases = load_cases(cases_path)
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    if not isinstance(protocol, Mapping):
        raise ValueError("protocol must be a mapping")
    assignments = _assignments(protocol)
    if set(assignments) != {case.case_id for case in cases}:
        raise ValueError("protocol assignments do not exactly cover the case stream")

    training: list[EvaluatorTrainingRow] = []
    calibration: list[tuple[str, str, Mapping[str, object], float]] = []
    dev_families: set[str] = set()
    cal_families: set[str] = set()
    for case in cases:
        outcomes = {row.intent_id: row for row in case.candidate_outcomes}
        partition = assignments[case.case_id]
        (dev_families if partition == "ghost_dev" else cal_families).add(case.family_id)
        for intent in case.intents:
            outcome = outcomes[intent.intent_id]
            features = pre_action_features(
                context=case.context,
                graph=case.graph,
                intent=intent,
            )
            target = _shadow_utility(outcome)
            if partition == "ghost_dev":
                training.append(EvaluatorTrainingRow(features, target))
            else:
                calibration.append((case.case_id, case.family_id, features, target))
    if dev_families & cal_families:
        raise ValueError("ghost_dev and ghost_cal families must be disjoint")

    evaluator = FrozenDeploymentEvaluator.fit(
        training,
        ridge=1.0,
        hash_buckets=512,
        training_provenance="ghost_dev_shadow_labels_only",
    )
    predictions = [evaluator.score(row[2]) for row in calibration]
    targets = [row[3] for row in calibration]
    by_family: dict[str, list[tuple[float, float]]] = defaultdict(list)
    by_case: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for (case_id, family_id, _features, target), prediction in zip(
        calibration, predictions, strict=True
    ):
        by_family[family_id].append((prediction, target))
        by_case[case_id].append((prediction, target))
    family_pairs = {
        key: (fmean(x for x, _ in rows), fmean(y for _, y in rows))
        for key, rows in by_family.items()
    }
    family_keys = tuple(sorted(family_pairs))
    family_correlation = _pearson(
        [family_pairs[key][0] for key in family_keys],
        [family_pairs[key][1] for key in family_keys],
    )
    rng = random.Random(bootstrap_seed)
    draws = []
    for _ in range(bootstrap_samples):
        chosen = [family_keys[rng.randrange(len(family_keys))] for _ in family_keys]
        draws.append(
            _pearson(
                [family_pairs[key][0] for key in chosen],
                [family_pairs[key][1] for key in chosen],
            )
        )
    lower = sorted(draws)[max(0, math.ceil(0.05 * len(draws)) - 1)]
    comparable = concordant = 0
    for rows in by_case.values():
        for index, current in enumerate(rows):
            for previous in rows[:index]:
                direct_delta = current[0] - previous[0]
                shadow_delta = current[1] - previous[1]
                if direct_delta == 0.0 or shadow_delta == 0.0:
                    continue
                comparable += 1
                concordant += int(direct_delta * shadow_delta > 0.0)
    concordance = 0.0 if not comparable else concordant / comparable
    thresholds = {
        "min_family_correlation": 0.2,
        "min_bootstrap_lower": 0.1,
        "min_pairwise_concordance": 0.55,
    }
    passed = (
        family_correlation >= thresholds["min_family_correlation"]
        and lower >= thresholds["min_bootstrap_lower"]
        and concordance >= thresholds["min_pairwise_concordance"]
    )
    payload: dict[str, object] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "decision": "PASS" if passed else "BLOCKED_FEEDBACK_NOT_IDENTIFIABLE",
        "model_calls": 0,
        "cases_file_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
        "protocol_file_sha256": hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
        "evaluator_snapshot_sha256": evaluator.snapshot_sha256,
        "evaluator_training_provenance": evaluator.training_provenance,
        "dev_case_count": sum(assignments[row.case_id] == "ghost_dev" for row in cases),
        "dev_family_count": len(dev_families),
        "dev_candidate_count": len(training),
        "cal_case_count": sum(assignments[row.case_id] == "ghost_cal" for row in cases),
        "cal_family_count": len(cal_families),
        "cal_candidate_count": len(calibration),
        "family_disjoint": True,
        "calibration_updates_evaluator": False,
        "runtime_features_use_gold": False,
        "feature_timing": "pre_action_only",
        "post_action_telemetry_used_for_selection": False,
        "shadow_used_for_dev_fit_and_cal_audit_only": True,
        "candidate_level_pearson": _pearson(predictions, targets),
        "family_macro_pearson": family_correlation,
        "family_bootstrap_lower_95_one_sided": lower,
        "within_case_pairwise_concordance": concordance,
        "comparable_pair_count": comparable,
        "thresholds": thresholds,
        "bootstrap_samples": bootstrap_samples,
        "bootstrap_seed": bootstrap_seed,
        "claim_scope": "dev_fitted_calibration_identifiability_not_sealed_test",
    }
    report = {**payload, "report_sha256": content_sha256(payload)}
    evaluator_output.parent.mkdir(parents=True, exist_ok=True)
    evaluator_output.write_text(
        json.dumps(evaluator.to_mapping(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report_output.parent.mkdir(parents=True, exist_ok=True)
    report_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--evaluator-output", type=Path, required=True)
    parser.add_argument("--report-output", type=Path, required=True)
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=24)
    args = parser.parse_args(argv)
    report = run_audit(
        cases_path=args.cases,
        protocol_path=args.protocol,
        evaluator_output=args.evaluator_output,
        report_output=args.report_output,
        bootstrap_samples=args.bootstrap_samples,
        bootstrap_seed=args.seed,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["decision"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["REPORT_SCHEMA_VERSION", "main", "run_audit"]
