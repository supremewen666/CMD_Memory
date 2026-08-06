#!/usr/bin/env python3
"""Run the zero-call SIGIL-QD V0 conditional-policy viability screen."""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
from statistics import fmean
from typing import Iterable, Mapping, Sequence

from cmd_audit.eval.descriptor_policy_value import (
    CrossFitPrediction,
    DescriptorPolicyCase,
    V0Decision,
    evaluate_descriptor_policy_value,
)


_RUNTIME_SURFACE = "tier2_item_gate"
_FORBIDDEN_CANDIDATES = frozenset({"seed:safety_error"})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", nargs="+", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--outer-folds", type=int, default=5)
    parser.add_argument("--minimum-training-cases", type=int, default=30)
    parser.add_argument("--minimum-training-families", type=int, default=10)
    parser.add_argument("--minimum-test-families", type=int, default=5)
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument("--bootstrap-seed", type=int, default=24)
    parser.add_argument("--elite-agreement-threshold", type=float, default=0.80)
    args = parser.parse_args()

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    all_cases: list[DescriptorPolicyCase] = []
    input_manifests = []
    indication_metadata: dict[str, Mapping[str, object]] = {}
    for path in args.inputs:
        resolved = path.expanduser().resolve()
        manifest, cases, indications = load_v0_input(resolved)
        all_cases.extend(cases)
        indication_metadata.update(indications)
        input_manifests.append(
            {
                "path": str(resolved),
                "sha256": file_sha256(resolved),
                "arena_id": manifest["arena_id"],
                "case_count": len(cases),
                "runtime_uses_gold": manifest["runtime_uses_gold"],
                "structural_extractor_version": manifest.get(
                    "structural_extractor_version"
                ),
                "evaluation_judge_identity": manifest.get(
                    "evaluation_judge_identity"
                ),
                "selection_judge_identity": manifest.get(
                    "selection_judge_identity"
                ),
            }
        )

    decision, predictions = evaluate_descriptor_policy_value(
        all_cases,
        outer_folds=args.outer_folds,
        minimum_training_cases=args.minimum_training_cases,
        minimum_training_families=args.minimum_training_families,
        minimum_test_families=args.minimum_test_families,
        bootstrap_samples=args.bootstrap_samples,
        bootstrap_seed=args.bootstrap_seed,
        elite_agreement_threshold=args.elite_agreement_threshold,
    )
    write_v0_artifacts(
        output_dir,
        cases=all_cases,
        predictions=predictions,
        decision=decision,
        input_manifests=input_manifests,
        indication_metadata=indication_metadata,
    )
    print(f"[RESULT] protocol={decision.protocol}")
    print(f"[RESULT] final_decision={decision.final_decision}")
    print(
        "[RESULT] domains="
        + ",".join(
            f"{row.domain_id}:{row.verdict}" for row in decision.domains
        )
    )
    print(f"[RESULT] output={output_dir / 'v0_claim_decision.json'}")
    return 0


def load_v0_input(
    path: Path,
) -> tuple[
    Mapping[str, object],
    tuple[DescriptorPolicyCase, ...],
    dict[str, Mapping[str, object]],
]:
    if not path.is_file():
        raise FileNotFoundError(path)
    rows = tuple(
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    manifests = tuple(
        row for row in rows if row.get("record_type") == "arena_manifest"
    )
    if len(manifests) != 1:
        raise ValueError(f"{path}: expected exactly one arena manifest")
    manifest = manifests[0]
    if manifest.get("runtime_uses_gold") is not False:
        raise ValueError(f"{path}: runtime_uses_gold must be false")
    arena_id = str(manifest.get("arena_id") or "")
    if not arena_id:
        raise ValueError(f"{path}: arena_id is required")

    observations = {
        str(row["case_id"]): row
        for row in rows
        if row.get("record_type") == "gold_free_observation"
    }
    if not observations:
        raise ValueError(f"{path}: no gold-free observations")
    branches = {
        str(row["case_id"]): str(row.get("runtime_branch") or "")
        for row in rows
        if row.get("record_type") == "top_p_saturation_event"
    }
    indications_by_case: dict[str, list[Mapping[str, object]]] = {}
    indication_metadata: dict[str, Mapping[str, object]] = {}
    for row in rows:
        if (
            row.get("record_type") != "structural_indication_event"
            or row.get("runtime_surface") != _RUNTIME_SURFACE
        ):
            continue
        case_id = str(row.get("case_id") or "")
        if case_id not in observations:
            raise ValueError(f"{path}: indication lacks outcome: {case_id}")
        if row.get("created_before_outcome") is not True:
            raise ValueError(f"{path}: indication is not pre-outcome")
        if row.get("route_selected") is not False:
            raise ValueError(f"{path}: V0 requires a shadow indication path")
        if row.get("scope_active") is not False:
            raise ValueError(f"{path}: V0 requires an inactive scope")
        indications_by_case.setdefault(case_id, []).append(row)

    cases = []
    for case_id, observation in sorted(observations.items()):
        runtime_branch = branches.get(case_id)
        if runtime_branch not in {"fix", "fill"}:
            scores = tuple(observation.get("shadow_gold_scores") or ())
            runtime_branch = "fix" if scores else "fill"
        candidate_gains = _finite_scores(
            observation.get("shadow_gold_scores") or ()
        )
        frozen_gain = optional_finite(
            observation.get("selected_shadow_gain")
        )
        frozen_skill = observation.get("selected_skill_id")
        descriptor_id, descriptor_meta = build_descriptor(
            indications_by_case.get(case_id, ())
        )
        indication_metadata[f"{arena_id}|{case_id}"] = descriptor_meta
        cases.append(
            DescriptorPolicyCase(
                case_id=f"{arena_id}|{case_id}",
                family_id=f"{arena_id}|{observation.get('family_id') or case_id}",
                domain_id=arena_id,
                descriptor_id=descriptor_id,
                runtime_branch=runtime_branch,
                candidate_gains=candidate_gains,
                frozen_skill_id=(
                    str(frozen_skill) if frozen_skill is not None else None
                ),
                frozen_gain=frozen_gain if frozen_gain is not None else 0.0,
                failure_type=str(observation.get("failure_type") or ""),
            )
        )
    return manifest, tuple(cases), indication_metadata


def build_descriptor(
    indications: Sequence[Mapping[str, object]],
) -> tuple[str, Mapping[str, object]]:
    """Build a descriptor without reading action, label, or evaluator fields."""
    components = []
    provenance = []
    for indication in indications:
        signal_type = str(indication.get("signal_type") or "")
        if not signal_type:
            raise ValueError("structural indication requires signal_type")
        strength = optional_finite(indication.get("strength"))
        if strength is None:
            raise ValueError("structural indication requires finite strength")
        component = f"{signal_type}:{strength_bucket(strength)}"
        components.append(component)
        provenance.append(
            {
                "signal_type": signal_type,
                "strength_bucket": strength_bucket(strength),
                "runtime_surface": str(indication["runtime_surface"]),
                "extractor_version": str(
                    indication.get("extractor_version") or ""
                ),
                "input_allowlist_sha256": str(
                    indication.get("input_allowlist_sha256") or ""
                ),
                "created_before_outcome": True,
            }
        )
    signature = tuple(sorted(set(components)))
    descriptor_id = (
        f"{_RUNTIME_SURFACE}|"
        + ("+".join(signature) if signature else "no_signal")
    )
    return descriptor_id, {
        "descriptor_id": descriptor_id,
        "components": list(signature),
        "provenance": provenance,
        "action_field_ignored": True,
        "failure_type_ignored": True,
    }


def strength_bucket(strength: float) -> str:
    if not 0.0 <= strength <= 1.0:
        raise ValueError("signal strength must be in [0, 1]")
    if strength < 0.50:
        return "low"
    if strength < 0.80:
        return "medium"
    if strength < 0.95:
        return "high"
    return "very_high"


def write_v0_artifacts(
    output_dir: Path,
    *,
    cases: Sequence[DescriptorPolicyCase],
    predictions: Sequence[CrossFitPrediction],
    decision: V0Decision,
    input_manifests: Sequence[Mapping[str, object]],
    indication_metadata: Mapping[str, Mapping[str, object]],
) -> None:
    manifest = {
        "protocol": decision.protocol,
        "inputs": list(input_manifests),
        "case_count": len(cases),
        "ordered_case_ids_sha256": text_sha256(
            "\n".join(row.case_id for row in cases)
        ),
        "runtime_uses_gold": False,
        "policy_fit_uses_post_outcome_candidate_utility": True,
        "failure_type_runtime_feature": False,
        "descriptor_uses_indication_action": False,
        "policy_unit": "single_executable_operator",
        "frozen_control": "recorded_gold_free_top1_or_abstention",
        "outer_folds": decision.outer_folds,
        "bootstrap_samples": decision.bootstrap_samples,
        "bootstrap_seed": decision.bootstrap_seed,
        "minimum_training_cases": decision.minimum_training_cases,
        "minimum_training_families": decision.minimum_training_families,
        "minimum_test_families": decision.minimum_test_families,
        "elite_agreement_threshold": decision.elite_agreement_threshold,
    }
    write_json(output_dir / "v0_manifest.json", manifest)
    write_json(
        output_dir / "descriptor_stability.json",
        {
            "status": "not_evaluable_single_extractor_run",
            "complete_signal_vector": False,
            "short_circuit_measurement": True,
            "assignment_reruns": 1,
            "note": (
                "V0 uses first-observed live indications only. V1 must collect "
                "non-short-circuit signal vectors and rerun stability."
            ),
            "descriptors": indication_metadata,
        },
    )
    write_json(
        output_dir / "v0_claim_decision.json",
        decision.to_dict(),
    )
    write_jsonl(
        output_dir / "crossfit_policy_predictions.jsonl",
        (asdict(row) for row in predictions),
    )
    write_descriptor_occupancy(
        output_dir / "descriptor_occupancy.csv",
        cases,
    )
    write_actuator_audit(
        output_dir / "operator_actuator_audit.csv",
        cases,
    )
    write_headroom(
        output_dir / "oracle_headroom_by_scope.csv",
        predictions,
    )
    write_elite_heterogeneity(
        output_dir / "elite_heterogeneity.csv",
        decision,
    )
    write_contrasts(
        output_dir / "paired_policy_contrasts.csv",
        decision,
    )
    write_protected_gates(
        output_dir / "protected_scope_gates.csv",
        decision,
    )


def write_descriptor_occupancy(
    path: Path,
    cases: Sequence[DescriptorPolicyCase],
) -> None:
    grouped: dict[tuple[str, str], list[DescriptorPolicyCase]] = {}
    for row in cases:
        grouped.setdefault((row.domain_id, row.descriptor_id), []).append(row)
    fields = [
        "domain_id",
        "descriptor_id",
        "cases",
        "families",
        "fix_cases",
        "fill_cases",
        "protected_cases",
    ]
    records = []
    for (domain_id, descriptor_id), rows in sorted(grouped.items()):
        records.append(
            {
                "domain_id": domain_id,
                "descriptor_id": descriptor_id,
                "cases": len(rows),
                "families": len({row.family_id for row in rows}),
                "fix_cases": sum(row.runtime_branch == "fix" for row in rows),
                "fill_cases": sum(row.runtime_branch == "fill" for row in rows),
                "protected_cases": sum(row.protected for row in rows),
            }
        )
    write_csv(path, fields, records)


def write_actuator_audit(
    path: Path,
    cases: Sequence[DescriptorPolicyCase],
) -> None:
    grouped: dict[tuple[str, str], list[float]] = {}
    family_sets: dict[tuple[str, str], set[str]] = {}
    for row in cases:
        if row.runtime_branch != "fix":
            continue
        for skill_id, gain in row.candidate_gains:
            key = (row.domain_id, skill_id)
            grouped.setdefault(key, []).append(gain)
            family_sets.setdefault(key, set()).add(row.family_id)
    records = []
    for (domain_id, skill_id), gains in sorted(grouped.items()):
        records.append(
            {
                "domain_id": domain_id,
                "skill_id": skill_id,
                "eligible_cases": len(gains),
                "families": len(family_sets[(domain_id, skill_id)]),
                "mean_gain": fmean(gains),
                "positive_recovery_rate": sum(
                    gain >= 0.1 for gain in gains
                )
                / len(gains),
                "nonfinite_rate": 0.0,
                "state_change_observed": "not_recorded_in_stage1_artifact",
                "cost_observed": "not_recorded_per_candidate",
            }
        )
    write_csv(
        path,
        [
            "domain_id",
            "skill_id",
            "eligible_cases",
            "families",
            "mean_gain",
            "positive_recovery_rate",
            "nonfinite_rate",
            "state_change_observed",
            "cost_observed",
        ],
        records,
    )


def write_headroom(
    path: Path,
    predictions: Sequence[CrossFitPrediction],
) -> None:
    grouped: dict[tuple[str, str], list[float]] = {}
    family_sets: dict[tuple[str, str], set[str]] = {}
    for row in predictions:
        if row.protected:
            continue
        key = (row.domain_id, row.descriptor_id)
        grouped.setdefault(key, []).append(row.oracle_gain - row.frozen_gain)
        family_sets.setdefault(key, set()).add(row.family_id)
    records = [
        {
            "domain_id": domain_id,
            "descriptor_id": descriptor_id,
            "cases": len(values),
            "families": len(family_sets[(domain_id, descriptor_id)]),
            "mean_oracle_headroom": fmean(values),
            "positive_headroom_rate": sum(value > 0 for value in values)
            / len(values),
        }
        for (domain_id, descriptor_id), values in sorted(grouped.items())
    ]
    write_csv(
        path,
        [
            "domain_id",
            "descriptor_id",
            "cases",
            "families",
            "mean_oracle_headroom",
            "positive_headroom_rate",
        ],
        records,
    )


def write_elite_heterogeneity(path: Path, decision: V0Decision) -> None:
    records = []
    for domain in decision.domains:
        for niche in domain.supported_niches:
            record = asdict(niche)
            record["fold_selections"] = "|".join(niche.fold_selections)
            records.append(record)
    write_csv(
        path,
        [
            "domain_id",
            "descriptor_id",
            "cases",
            "families",
            "minimum_test_families",
            "fold_selections",
            "modal_elite",
            "modal_agreement",
            "supported",
            "stable",
        ],
        records,
    )


def write_contrasts(path: Path, decision: V0Decision) -> None:
    records = []
    for domain in decision.domains:
        for contrast in (
            domain.headroom,
            domain.descriptor_vs_frozen,
            domain.descriptor_vs_unkeyed,
            domain.descriptor_vs_random,
        ):
            records.append(
                {"domain_id": domain.domain_id, **asdict(contrast)}
            )
    write_csv(
        path,
        [
            "domain_id",
            "treatment",
            "control",
            "estimate",
            "lower_bound_95_one_sided",
            "families",
            "passed",
        ],
        records,
    )


def write_protected_gates(path: Path, decision: V0Decision) -> None:
    records = []
    for row in decision.domains:
        records.extend(
            [
                {
                    "domain_id": row.domain_id,
                    "gate": "scope_external_lower_bound",
                    "value": row.scope_external_lower_bound,
                    "passed": row.scope_external_lower_bound >= -0.05,
                },
                {
                    "domain_id": row.domain_id,
                    "gate": "unseen_family_lower_bound",
                    "value": row.unseen_family_lower_bound,
                    "passed": row.unseen_family_lower_bound >= -0.05,
                },
                {
                    "domain_id": row.domain_id,
                    "gate": "null_fill_exact",
                    "value": int(row.null_fill_exact),
                    "passed": row.null_fill_exact,
                },
                {
                    "domain_id": row.domain_id,
                    "gate": "anchor_regressions",
                    "value": row.anchor_regressions,
                    "passed": row.anchor_regressions == 0,
                },
                {
                    "domain_id": row.domain_id,
                    "gate": "budget_alignment_rate",
                    "value": row.budget_alignment_rate,
                    "passed": row.budget_alignment_rate == 1.0,
                },
            ]
        )
    write_csv(path, ["domain_id", "gate", "value", "passed"], records)


def _finite_scores(values: Iterable[Sequence[object]]) -> tuple[tuple[str, float], ...]:
    result = []
    seen = set()
    for value in values:
        if len(value) != 2:
            raise ValueError("candidate score must be [skill_id, gain]")
        skill_id = str(value[0])
        if skill_id in _FORBIDDEN_CANDIDATES:
            continue
        gain = optional_finite(value[1])
        if gain is None:
            continue
        if skill_id in seen:
            raise ValueError(f"duplicate candidate score: {skill_id}")
        seen.add(skill_id)
        result.append((skill_id, gain))
    return tuple(sorted(result))


def optional_finite(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, values: Iterable[Mapping[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for value in values:
            handle.write(
                json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    allow_nan=False,
                )
                + "\n"
            )


def write_csv(
    path: Path,
    fields: Sequence[str],
    records: Iterable[Mapping[str, object]],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
