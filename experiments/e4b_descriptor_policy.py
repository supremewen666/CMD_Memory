#!/usr/bin/env python3
"""E4b: cross-fitted descriptor/random/unkeyed policy-value experiment.

This is the claim-bearing V4 adapter for the existing
``descriptor_policy_value`` estimator.  Runtime descriptors are built only
from the frozen dev-prefix semantic vocabulary, signal signature and runtime
surface.  Candidate utilities are post-outcome shadow measurements used only
inside family-blocked cross-fitting; they never enter a runtime decision.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Mapping, Sequence

from cmd_audit.eval.descriptor_policy_value import (
    CrossFitPrediction,
    DescriptorPolicyCase,
    evaluate_descriptor_policy_value,
)
from cmd_audit.repair.ghost_ecology import (
    EcologyLedger,
    GhostEcology,
    NicheObservation,
    NicheObserver,
    is_legal_niche_transition,
)
from cmd_audit.repair.niche_archive import (
    BehaviorDescriptor,
    SemanticClusterVocabulary,
)
from experiments.analyze_descriptor_policy_value import (
    write_contrasts,
    write_descriptor_occupancy,
    write_elite_heterogeneity,
    write_protected_gates,
)
from experiments.v4_prequential_runner import V4PrequentialCase, load_cases
from experiments.ghost_ecology_zero_call import record_zero_call_ecology_window


E4B_SCHEMA_VERSION = "cmd-e4b-descriptor-policy-v1"
E4B_MANIFEST_SCHEMA_VERSION = "cmd-e4b-descriptor-policy-manifest-v1"
HEADLINE_ARMS = ("descriptor", "random", "unkeyed")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a JSON object")
    return value


def _utility(outcome: object, *, locality_penalty: float, change_penalty: float) -> float:
    if not outcome.valid or outcome.rolled_back:
        return -1.0
    return float(outcome.recovery_gain) - locality_penalty * float(
        outcome.locality_cost
    ) - change_penalty * int(outcome.changed_item_count)


def _dev_prefix_size(case_count: int, fraction: float) -> int:
    if not 0.0 < fraction <= 1.0:
        raise ValueError("dev_prefix_fraction must lie in (0, 1]")
    return min(case_count, max(1, int(case_count * fraction)))


def build_policy_cases(
    cases: Sequence[V4PrequentialCase],
    *,
    candidate_budget: int,
    dev_prefix_fraction: float,
    locality_penalty: float,
    change_penalty: float,
) -> tuple[
    tuple[DescriptorPolicyCase, ...],
    tuple[dict[str, object], ...],
    SemanticClusterVocabulary,
]:
    if not cases:
        raise ValueError("E4b requires cases")
    if isinstance(candidate_budget, bool) or not isinstance(candidate_budget, int) or candidate_budget < 1:
        raise ValueError("candidate_budget must be a positive integer")
    for value, name in ((locality_penalty, "locality_penalty"), (change_penalty, "change_penalty")):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"{name} must be a non-negative number")
    indexes = tuple(row.context.event_index for row in cases)
    if indexes != tuple(sorted(indexes)) or len(set(indexes)) != len(indexes):
        raise ValueError("E4b cases must have unique prequential event indexes in order")
    if any(len(row.intents) != candidate_budget for row in cases):
        raise ValueError("E4b candidate budget is not aligned across cases")
    prefix = _dev_prefix_size(len(cases), dev_prefix_fraction)
    vocabulary = SemanticClusterVocabulary(
        tuple(row.context.semantic_cluster for row in cases[:prefix]),
        source="dev-prefix",
    ).freeze()
    policy_rows: list[DescriptorPolicyCase] = []
    assignments: list[dict[str, object]] = []
    for case in cases:
        descriptor = BehaviorDescriptor.from_semantic_cluster(
            case.context.semantic_cluster,
            vocabulary=vocabulary,
            signal_signature=(case.context.signal_signature,),
            runtime_surface=case.context.runtime_surface,
        )
        outcomes = {row.intent_id: row for row in case.candidate_outcomes}
        effect_gains: dict[str, float] = {}
        for intent in case.intents:
            gain = _utility(
                outcomes[intent.intent_id],
                locality_penalty=float(locality_penalty),
                change_penalty=float(change_penalty),
            )
            effect_gains[intent.effect] = max(effect_gains.get(intent.effect, -1.0), gain)
        legacy_intent = next(
            row for row in case.intents if row.intent_id == case.legacy_intent_id
        )
        frozen_gain = _utility(
            outcomes[legacy_intent.intent_id],
            locality_penalty=float(locality_penalty),
            change_penalty=float(change_penalty),
        )
        policy_rows.append(
            DescriptorPolicyCase(
                case_id=case.case_id,
                family_id=case.family_id,
                domain_id=case.context.domain,
                descriptor_id=descriptor.niche_id,
                runtime_branch="fix",
                candidate_gains=tuple(sorted(effect_gains.items())),
                frozen_skill_id=legacy_intent.effect,
                frozen_gain=frozen_gain,
                failure_type="",
            )
        )
        assignment_body = {
            "schema_version": E4B_SCHEMA_VERSION,
            "case_id": case.case_id,
            "family_id": case.family_id,
            "event_index": case.context.event_index,
            "domain_id": case.context.domain,
            "descriptor_id": descriptor.niche_id,
            "memory_fingerprint_cluster": descriptor.memory_fingerprint_cluster,
            "signal_signature": list(descriptor.signal_signature),
            "runtime_surface": descriptor.runtime_surface,
            "descriptor_version": descriptor.version,
            "candidate_effects": sorted(effect_gains),
            "candidate_budget": candidate_budget,
            "post_outcome_fields_excluded_from_descriptor": True,
        }
        assignments.append(
            {
                **assignment_body,
                "assignment_sha256": hashlib.sha256(_canonical(assignment_body)).hexdigest(),
            }
        )
    return tuple(policy_rows), tuple(assignments), vocabulary


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.write_text(
        "".join(_canonical(row).decode("utf-8") + "\n" for row in rows),
        encoding="utf-8",
    )


def record_e4b_ecology_trace(
    *,
    policy_rows: Sequence[DescriptorPolicyCase],
    predictions: Sequence[CrossFitPrediction],
    ledger_path: Path,
    summary_path: Path,
    window_size: int,
) -> dict[str, object]:
    """Record descriptor-niche snapshots/pressure without changing policy value.

    The observations are post-outcome descriptive rows derived from the
    already cross-fitted predictions.  They never enter descriptor creation,
    policy fitting, or the E4b headline decision.  Illegal lifecycle jumps are
    counted and rejected before a ledger write.
    """
    if isinstance(window_size, bool) or not isinstance(window_size, int) or window_size < 1:
        raise ValueError("ecology_window_size must be a positive integer")
    row_by_case = {row.case_id: row for row in policy_rows}
    if len(row_by_case) != len(policy_rows):
        raise ValueError("E4b ecology requires unique policy case IDs")
    prediction_by_case = {row.case_id: row for row in predictions}
    if len(prediction_by_case) != len(predictions) or set(prediction_by_case) != set(row_by_case):
        raise ValueError("E4b ecology predictions must exactly cover policy cases")
    order = {row.case_id: index for index, row in enumerate(policy_rows)}
    grouped: dict[str, list[CrossFitPrediction]] = defaultdict(list)
    for prediction in predictions:
        grouped[prediction.descriptor_id].append(prediction)

    ledger = EcologyLedger(ledger_path)
    ecology = GhostEcology(ledger)
    rejected: Counter[str] = Counter()
    transition_proposals = 0
    legal_transitions = 0
    checkpoint_count = 0
    for descriptor_id in sorted(grouped):
        rows = sorted(grouped[descriptor_id], key=lambda row: order[row.case_id])
        observations = tuple(
            NicheObservation(
                failure_id=row.case_id,
                pattern_revision_id=descriptor_id,
                skill_revision_id=row.descriptor_skill_id,
                responsibility=1.0,
                selected=row.descriptor_skill_id is not None,
                success=max(0.0, min(1.0, (float(row.descriptor_gain) + 1.0) / 2.0)),
                resolved=(
                    row.budget_aligned
                    and float(row.descriptor_gain) >= float(row.frozen_gain)
                ),
            )
            for row in rows
        )
        stops = {1, len(rows)}
        stops.update(range(window_size, len(rows) + 1, window_size))
        previous = None
        for stop in sorted(stops):
            prefix = observations[:stop]
            previous_state = "latent" if previous is None else previous.state
            proposed = NicheObserver().snapshot(
                pattern_revision_id=descriptor_id,
                observations=prefix,
                window_start=0,
                window_end=stop - 1,
                previous_state=previous_state,
            )
            if previous is not None and proposed.state != previous.state:
                transition_proposals += 1
                if not is_legal_niche_transition(previous.state, proposed.state):
                    rejected[f"{previous.state}->{proposed.state}"] += 1
                    continue
                legal_transitions += 1
            window = record_zero_call_ecology_window(
                ecology,
                pattern_revision_id=descriptor_id,
                observations=prefix,
                window_start=0,
                window_end=stop - 1,
                event_index=ledger.last_event_index + 1,
                previous_snapshot=previous,
                previous_state=previous_state,
                unmatched_responsibilities=tuple(
                    1.0 if row.descriptor_skill_id is None else 0.0
                    for row in rows[:stop]
                ),
                abstentions=tuple(
                    row.descriptor_skill_id is None for row in rows[:stop]
                ),
                prediction_residuals=tuple(
                    float(row.oracle_gain) - float(row.descriptor_gain)
                    for row in rows[:stop]
                ),
            )
            previous = window.snapshot
            checkpoint_count += 1

    events = ledger.events
    event_types = Counter(str(row["event_type"]) for row in events)
    state_counts = Counter(
        str(row["payload"]["state"])
        for row in events
        if row["event_type"] == "niche_snapshot"
    )
    transition_counts = Counter(
        f"{row['payload']['from_state']}->{row['payload']['to_state']}"
        for row in events
        if row["event_type"] == "niche_transition"
    )
    pressure_counts = Counter(
        str(row["payload"]["proposal_kind"])
        for row in events
        if row["event_type"] == "discovery_pressure"
    )
    denominator = legal_transitions + sum(rejected.values())
    summary = {
        "schema_version": "cmd-e4b-ecology-trace-v1",
        "descriptor_count": len(grouped),
        "case_count": len(policy_rows),
        "window_size": window_size,
        "checkpoint_count": checkpoint_count,
        "ledger_event_count": len(events),
        "ledger_head_sha256": ledger.head_sha256,
        "event_type_counts": dict(sorted(event_types.items())),
        "niche_state_counts": dict(sorted(state_counts.items())),
        "transition_counts": dict(sorted(transition_counts.items())),
        "discovery_pressure_counts": dict(sorted(pressure_counts.items())),
        "transition_proposal_count": denominator,
        "legal_transition_count": legal_transitions,
        "rejected_transition_count": sum(rejected.values()),
        "rejected_transition_counts": dict(sorted(rejected.items())),
        "legal_transition_rate": (
            None if denominator == 0 else legal_transitions / denominator
        ),
        "post_outcome_descriptive_only": True,
        "affects_headline_decision": False,
        "model_calls": 0,
        "network_calls": 0,
    }
    _write_json(summary_path, summary)
    return summary


def run_e4b(
    *,
    cases_path: Path,
    output_dir: Path,
    candidate_budget: int,
    dev_prefix_fraction: float = 0.20,
    outer_folds: int = 5,
    minimum_training_cases: int = 30,
    minimum_training_families: int = 10,
    minimum_test_families: int = 5,
    bootstrap_samples: int = 10_000,
    bootstrap_seed: int = 24,
    elite_agreement_threshold: float = 0.80,
    safety_margin: float = -0.05,
    locality_penalty: float = 1.0,
    change_penalty: float = 0.05,
    materialization_manifest: Path | None = None,
    ecology_window_size: int = 50,
) -> dict[str, object]:
    if output_dir.exists():
        raise ValueError(f"refusing to overwrite E4b output directory: {output_dir}")
    if not cases_path.is_file():
        raise FileNotFoundError(cases_path)
    cases = load_cases(cases_path)
    policy_rows, assignments, vocabulary = build_policy_cases(
        cases,
        candidate_budget=candidate_budget,
        dev_prefix_fraction=dev_prefix_fraction,
        locality_penalty=locality_penalty,
        change_penalty=change_penalty,
    )
    manifest_binding = None
    if materialization_manifest is not None:
        manifest_binding = _mapping(
            json.loads(materialization_manifest.read_text(encoding="utf-8")),
            "materialization manifest",
        )
        if manifest_binding.get("output_sha256") != _file_sha256(cases_path):
            raise ValueError("materialization manifest does not bind E4b cases")
    decision, predictions = evaluate_descriptor_policy_value(
        policy_rows,
        outer_folds=outer_folds,
        minimum_training_cases=minimum_training_cases,
        minimum_training_families=minimum_training_families,
        minimum_test_families=minimum_test_families,
        bootstrap_samples=bootstrap_samples,
        bootstrap_seed=bootstrap_seed,
        elite_agreement_threshold=elite_agreement_threshold,
        safety_margin=safety_margin,
    )
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
    )
    try:
        ecology_summary = record_e4b_ecology_trace(
            policy_rows=policy_rows,
            predictions=predictions,
            ledger_path=staged / "ecology_ledger.jsonl",
            summary_path=staged / "ecology_summary.json",
            window_size=ecology_window_size,
        )
        manifest = {
            "schema_version": E4B_MANIFEST_SCHEMA_VERSION,
            "protocol": decision.protocol,
            "cases_path": str(cases_path.resolve()),
            "cases_sha256": _file_sha256(cases_path),
            "materialization_manifest": (
                None
                if materialization_manifest is None
                else str(materialization_manifest.resolve())
            ),
            "materialization_manifest_sha256": (
                None
                if materialization_manifest is None
                else _file_sha256(materialization_manifest)
            ),
            "case_count": len(cases),
            "family_count": len({row.family_id for row in cases}),
            "candidate_budget": candidate_budget,
            "headline_arms": list(HEADLINE_ARMS),
            "safety_control": "all_frozen",
            "descriptor_key": "BehaviorDescriptor.niche_id",
            "random_key": "seeded_case_order_independent_descriptor_permutation",
            "unkeyed_key": "single_global_pool",
            "semantic_cluster_vocabulary": vocabulary.to_manifest(),
            "dev_prefix_fraction": dev_prefix_fraction,
            "dev_prefix_case_count": _dev_prefix_size(len(cases), dev_prefix_fraction),
            "outer_folds": outer_folds,
            "minimum_training_cases": minimum_training_cases,
            "minimum_training_families": minimum_training_families,
            "minimum_test_families": minimum_test_families,
            "bootstrap_samples": bootstrap_samples,
            "bootstrap_seed": bootstrap_seed,
            "elite_agreement_threshold": elite_agreement_threshold,
            "safety_margin": safety_margin,
            "locality_penalty": locality_penalty,
            "change_penalty": change_penalty,
            "ecology_window_size": ecology_window_size,
            "ecology_summary": ecology_summary,
            "runtime_uses_gold": False,
            "shadow_candidate_utility_used_for_crossfit_outcome_only": True,
            "failure_type_used_by_descriptor": False,
            "model_calls": 0,
            "network_calls": 0,
            "final_decision": decision.final_decision,
        }
        _write_json(staged / "manifest.json", manifest)
        _write_json(staged / "decision.json", decision.to_dict())
        _write_jsonl(
            staged / "descriptor_assignments.jsonl", assignments
        )
        _write_jsonl(
            staged / "crossfit_predictions.jsonl",
            tuple(asdict(row) for row in predictions),
        )
        write_descriptor_occupancy(staged / "descriptor_occupancy.csv", policy_rows)
        write_contrasts(staged / "paired_policy_contrasts.csv", decision)
        write_elite_heterogeneity(staged / "elite_heterogeneity.csv", decision)
        write_protected_gates(staged / "protected_scope_gates.csv", decision)
        artifact_hashes = {
            path.name: _file_sha256(path)
            for path in sorted(staged.iterdir())
            if path.is_file()
        }
        _write_json(
            staged / "artifact_index.json",
            {
                "schema_version": E4B_SCHEMA_VERSION,
                "artifact_sha256": artifact_hashes,
            },
        )
        os.rename(staged, output_dir)
    except Exception:
        shutil.rmtree(staged, ignore_errors=True)
        raise
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--candidate-budget", type=int, required=True)
    parser.add_argument("--materialization-manifest", type=Path, required=True)
    parser.add_argument("--dev-prefix-fraction", type=float, default=0.20)
    parser.add_argument("--outer-folds", type=int, default=5)
    parser.add_argument("--minimum-training-cases", type=int, default=30)
    parser.add_argument("--minimum-training-families", type=int, default=10)
    parser.add_argument("--minimum-test-families", type=int, default=5)
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument("--bootstrap-seed", type=int, default=24)
    parser.add_argument("--elite-agreement-threshold", type=float, default=0.80)
    parser.add_argument("--safety-margin", type=float, default=-0.05)
    parser.add_argument("--locality-penalty", type=float, default=1.0)
    parser.add_argument("--change-penalty", type=float, default=0.05)
    parser.add_argument("--ecology-window-size", type=int, default=50)
    args = parser.parse_args(argv)
    result = run_e4b(
        cases_path=args.cases,
        output_dir=args.output_dir,
        candidate_budget=args.candidate_budget,
        dev_prefix_fraction=args.dev_prefix_fraction,
        outer_folds=args.outer_folds,
        minimum_training_cases=args.minimum_training_cases,
        minimum_training_families=args.minimum_training_families,
        minimum_test_families=args.minimum_test_families,
        bootstrap_samples=args.bootstrap_samples,
        bootstrap_seed=args.bootstrap_seed,
        elite_agreement_threshold=args.elite_agreement_threshold,
        safety_margin=args.safety_margin,
        locality_penalty=args.locality_penalty,
        change_penalty=args.change_penalty,
        materialization_manifest=args.materialization_manifest,
        ecology_window_size=args.ecology_window_size,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "E4B_MANIFEST_SCHEMA_VERSION",
    "E4B_SCHEMA_VERSION",
    "HEADLINE_ARMS",
    "build_policy_cases",
    "record_e4b_ecology_trace",
    "run_e4b",
]
