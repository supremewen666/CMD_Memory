#!/usr/bin/env python3
"""Descriptive analysis for gold-free, ecology, and chain arena artifacts."""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from cmd_audit.core.math_utils import (
    is_finite_number as _finite,
    mean_finite as _mean,
)
from cmd_audit.eval.gold_free_identifiability import (
    CaseRankingInput,
    analyze_gold_free_agreement,
    case_ranking_from_mappings,
)
from cmd_audit.eval.paired_stats import (
    BOOTSTRAP_ITERATIONS,
    bootstrap_paired_diff,
    mcnemar_exact_p,
    sign_test_p,
    wilson_interval,
)
from experiments.arena_runner_common import (
    ARENA_DATASET_FINGERPRINT_VERSION,
    arena_case_ids_sha256,
    arena_file_sha256,
)

DEFAULT_INPUTS = (
    "artifacts/arena/memtrace_observations.jsonl",
    "artifacts/arena/memfail_observations.jsonl",
    "artifacts/arena/stale_observations.jsonl",
)

# Fixed so a rerun of the analyzer reproduces the same intervals from the same
# artifacts. Analysis-only; unrelated to any arena run seed.
BOOTSTRAP_SEED = 20260807


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", nargs="+", default=DEFAULT_INPUTS)
    parser.add_argument("--output-dir", default="artifacts/arena/analysis")
    args = parser.parse_args()

    records = _load_artifacts(tuple(Path(value) for value in args.inputs))
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)

    manifests = records.get("arena_manifest", [])
    observations = records.get("gold_free_observation", [])
    snapshots = records.get("ecology_snapshot", [])
    attempts = records.get("chain_attempt", [])
    coactivation = records.get("coactivation_snapshot", [])
    deposition_candidates = records.get("deposition_candidate_event", [])
    deposition_confirmations = records.get(
        "deposition_confirmation_event",
        [],
    )
    depositions = records.get("chain_deposition_event", [])
    anti_patterns = records.get("anti_pattern_event", [])
    perturbations = records.get("perturbation_event", [])
    saturation = records.get("top_p_saturation_event", [])
    arm_comparisons = records.get("arena_arm_comparison_event", [])

    paths = []
    paths.append(
        _write_csv(
            output / "signal_by_failure.csv",
            _signal_slices(observations, ("arena_id", "failure_type")),
        )
    )
    paths.append(
        _write_csv(
            output / "signal_by_probe_coordinates.csv",
            _coordinate_slices(observations),
        )
    )
    paths.append(
        _write_csv(
            output / "saturation_summary.csv",
            _saturation_summary(saturation),
        )
    )
    paths.append(
        _write_csv(
            output / "skill_contribution.csv",
            _skill_contribution(saturation),
        )
    )
    paths.append(
        _write_csv(
            output / "cmd_vs_best_of_n.csv",
            _arm_comparison_summary(arm_comparisons),
        )
    )
    paths.append(
        _write_csv(
            output / "cmd_vs_best_of_n_by_budget.csv",
            _arm_comparison_by_budget(arm_comparisons),
        )
    )
    case_families = _case_families(observations)
    significance = _arm_significance_summary(
        arm_comparisons,
        families=case_families,
    )
    paths.append(
        _write_csv(
            output / "cmd_vs_best_of_n_significance.csv",
            significance,
        )
    )
    calibration = _self_assessment_calibration(observations)
    paths.append(
        _write_csv(
            output / "self_assessment_calibration.csv",
            calibration,
        )
    )
    null_protection = _null_protection_calibration(observations)
    paths.append(
        _write_csv(
            output / "null_protection_calibration.csv",
            null_protection,
        )
    )
    abstention_curve = _abstention_curve_by_failure(observations)
    paths.append(
        _write_csv(
            output / "abstention_curve_by_failure.csv",
            abstention_curve,
        )
    )
    applicability = _operator_applicability(observations)
    paths.append(
        _write_csv(
            output / "operator_applicability.csv",
            applicability,
        )
    )
    effective_field = _layer_effective_field(observations)
    paths.append(
        _write_csv(
            output / "layer_effective_field.csv",
            effective_field,
        )
    )
    stuffing_significance = _arm_significance_summary(
        arm_comparisons,
        families=case_families,
        control_field=CONTEXT_STUFFING_GAIN_FIELD,
    )
    paths.append(
        _write_csv(
            output / "cmd_vs_context_stuffing_significance.csv",
            stuffing_significance,
        )
    )
    niche_rows, overlap_rows, succession_rows = _ecology_tables(snapshots)
    paths.append(_write_csv(output / "niche_profiles.csv", niche_rows))
    paths.append(_write_csv(output / "niche_overlap.csv", overlap_rows))
    paths.append(_write_csv(output / "succession.csv", succession_rows))
    paths.append(
        _write_csv(
            output / "cross_arena_niche_reproducibility.csv",
            _cross_arena_niche_similarity(snapshots),
        )
    )
    paths.append(
        _write_csv(output / "chain_benefit_spectrum.csv", _chain_spectrum(attempts))
    )
    paths.append(
        _write_csv(output / "chain_directionality.csv", _directionality(attempts))
    )
    paths.append(
        _write_csv(
            output / "coactivation_edges.csv",
            _flatten_coactivation(coactivation),
        )
    )
    paths.append(_write_csv(output / "depositions.csv", depositions))
    paths.append(
        _write_csv(
            output / "deposition_candidates.csv",
            deposition_candidates,
        )
    )
    paths.append(
        _write_csv(
            output / "deposition_confirmations.csv",
            deposition_confirmations,
        )
    )
    paths.append(_write_csv(output / "anti_patterns.csv", anti_patterns))
    paths.append(
        _write_csv(
            output / "perturbation_response.csv",
            _flatten_perturbations(perturbations),
        )
    )

    summary = {
        # The design stays observational: arms were not randomized over a
        # pre-registered case stream, so a small p-value here bounds sampling
        # noise, not confounding. Which tier the evidence sits in is a property
        # of the design, not of whether tests were computed, so the two are
        # recorded separately rather than collapsed into one flag.
        "analysis_kind": "descriptive_observational",
        "hypothesis_tests_run": True,
        "hypothesis_test_role": "descriptive_not_confirmatory",
        "hypothesis_test_family": "paired_sign_test_and_family_blocked_bootstrap",
        "bootstrap_seed": BOOTSTRAP_SEED,
        "bootstrap_iterations": BOOTSTRAP_ITERATIONS,
        # Recorded so the curve is readable as a fixed sweep. Any tau adopted
        # into the runtime still has to be chosen on one seed and confirmed on
        # the others; this grid is descriptive only.
        "abstention_thresholds": list(ABSTENTION_THRESHOLDS),
        "arena_count": len(manifests),
        "case_observations": len(observations),
        "saturation_events": len(saturation),
        "arm_comparison_events": len(arm_comparisons),
        "ecology_snapshots": len(snapshots),
        "chain_attempts": len(attempts),
        "deposition_events": len(depositions),
        "deposition_candidate_events": len(deposition_candidates),
        "deposition_confirmation_events": len(deposition_confirmations),
        "anti_pattern_events": len(anti_patterns),
        "perturbation_events": len(perturbations),
        "source_manifests": manifests,
        "outputs": [str(path) for path in paths],
    }
    manifest_path = output / "analysis_manifest.json"
    manifest_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print("[RESULT] analysis_kind=descriptive_observational")
    print(f"[RESULT] arenas={len(manifests)}")
    print(f"[RESULT] observations={len(observations)}")
    print(f"[RESULT] saturation_events={len(saturation)}")
    print(f"[RESULT] arm_comparison_events={len(arm_comparisons)}")
    print(f"[RESULT] chain_attempts={len(attempts)}")
    print(
        "[RESULT] hypothesis_tests_run="
        f"{len(significance) + len(stuffing_significance)}"
        " role=descriptive_not_confirmatory"
    )
    print(
        "[RESULT] context_stuffing_pairs="
        f"{sum(int(row['n_paired']) for row in stuffing_significance)}"
    )
    degenerate = [
        row for row in effective_field if int(row["never_applicable_operators"])
    ]
    print(
        "[RESULT] layers_with_inapplicable_operators="
        f"{len(degenerate)}/{len(effective_field)}"
    )
    no_fault = [row for row in null_protection if row["case_kind"] == "no_fault"]
    no_fault_cases = sum(int(row["n"]) for row in no_fault)
    false_positives = sum(
        float(row["null_false_positive_rate"]) * int(row["n"])
        for row in no_fault
    )
    print(f"[RESULT] no_fault_cases={no_fault_cases}")
    print(
        "[RESULT] null_false_positive_rate="
        + (
            f"{false_positives / no_fault_cases:.6f}"
            if no_fault_cases
            else "nan"
        )
    )
    print(f"[RESULT] output_manifest={manifest_path}")
    return 0


def _load_artifacts(
    paths: Sequence[Path],
) -> dict[str, list[dict[str, object]]]:
    records: dict[str, list[dict[str, object]]] = {}
    artifacts: list[
        tuple[Path, dict[str, object], list[dict[str, object]]]
    ] = []
    seen_paths: set[Path] = set()
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(path)
        resolved_path = path.resolve()
        if resolved_path in seen_paths:
            raise ValueError(f"duplicate artifact path: {path}")
        seen_paths.add(resolved_path)
        file_rows: list[dict[str, object]] = []
        manifest_rows: list[dict[str, object]] = []
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                row = json.loads(line)
                record_type = str(row.get("record_type", ""))
                if not record_type:
                    raise ValueError(f"{path}:{line_number}: missing record_type")
                if record_type == "arena_manifest":
                    manifest_rows.append(row)
                    arena_id = str(row["arena_id"])
                    if row.get("runtime_uses_gold") is not False:
                        raise ValueError(
                            f"{arena_id}: runtime_uses_gold must be false"
                        )
                file_rows.append(row)
        if len(manifest_rows) != 1:
            raise ValueError(f"{path}: expected exactly one arena manifest")
        manifest = manifest_rows[0]
        _validate_dataset_provenance(path, manifest, file_rows)
        _validate_evolution_provenance(path, manifest, file_rows)
        artifacts.append((path, manifest, file_rows))

    arena_counts: dict[str, int] = {}
    for _path, manifest, _rows in artifacts:
        arena_id = str(manifest["arena_id"])
        arena_counts[arena_id] = arena_counts.get(arena_id, 0) + 1

    run_id_counts: dict[str, int] = {}
    for _path, manifest, file_rows in artifacts:
        arena_id = str(manifest["arena_id"])
        run_id = arena_id
        if arena_counts[arena_id] > 1:
            seed = manifest.get("seed", "unknown")
            base_run_id = f"{arena_id}_seed{seed}"
            occurrence = run_id_counts.get(base_run_id, 0) + 1
            run_id_counts[base_run_id] = occurrence
            run_id = (
                base_run_id
                if occurrence == 1
                else f"{base_run_id}_rep{occurrence}"
            )
        for row in _namespace_artifact_rows(file_rows, arena_id, run_id):
            records.setdefault(str(row["record_type"]), []).append(row)
    return records


def _namespace_artifact_rows(
    rows: Sequence[Mapping[str, object]],
    arena_id: str,
    run_id: str,
) -> list[dict[str, object]]:
    """Give replicated arena artifacts distinct analysis-time identities."""
    output: list[dict[str, object]] = []
    for source_row in rows:
        row = dict(source_row)
        if run_id != arena_id:
            if str(row.get("arena_id", "")) == arena_id:
                row["arena_id"] = run_id
            checkpoint = row.get("checkpoint")
            if isinstance(checkpoint, str) and (
                checkpoint == arena_id or checkpoint.startswith(f"{arena_id}:")
            ):
                row["checkpoint"] = f"{run_id}{checkpoint[len(arena_id):]}"
            if row.get("record_type") == "arena_manifest":
                row["arena_family"] = arena_id
        output.append(row)
    return output


def _validate_dataset_provenance(
    artifact_path: Path,
    manifest: Mapping[str, object],
    rows: Sequence[Mapping[str, object]],
) -> None:
    arena_id = str(manifest["arena_id"])
    if (
        manifest.get("dataset_fingerprint_version")
        != ARENA_DATASET_FINGERPRINT_VERSION
    ):
        raise ValueError(
            f"{arena_id}: missing or unsupported dataset fingerprint"
        )
    if manifest.get("dataset_source_kind") != "file":
        raise ValueError(f"{arena_id}: arena analysis requires a file dataset source")
    source_path_value = str(manifest.get("dataset_source_path", ""))
    if not source_path_value:
        raise ValueError(f"{arena_id}: missing dataset_source_path")
    for key in (
        "dataset_source_sha256",
        "selected_case_ids_sha256",
        "selected_cases_sha256",
    ):
        if not _is_sha256(manifest.get(key)):
            raise ValueError(f"{arena_id}: invalid {key}")
    try:
        source_size = int(manifest["dataset_source_size_bytes"])
        case_count = int(manifest["case_count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"{arena_id}: invalid dataset size or case count"
        ) from exc
    if source_size < 0 or case_count <= 0:
        raise ValueError(f"{arena_id}: invalid dataset size or case count")

    event_case_ids = [
        str(row["case_id"])
        for row in rows
        if row.get("record_type") == "top_p_saturation_event"
    ]
    if len(event_case_ids) != case_count:
        raise ValueError(
            f"{arena_id}: manifest case_count={case_count} but artifact has "
            f"{len(event_case_ids)} case events"
        )
    if (
        arena_case_ids_sha256(event_case_ids)
        != manifest["selected_case_ids_sha256"]
    ):
        raise ValueError(
            f"{arena_id}: artifact case ids do not match dataset fingerprint"
        )

    source_path = Path(source_path_value)
    if source_path.is_file():
        if source_path.stat().st_size != source_size:
            raise ValueError(
                f"{arena_id}: current dataset size differs from manifest"
            )
        if arena_file_sha256(source_path) != manifest["dataset_source_sha256"]:
            raise ValueError(
                f"{arena_id}: current dataset bytes differ from manifest"
            )


def _is_sha256(value: object) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


def _validate_evolution_provenance(
    artifact_path: Path,
    manifest: Mapping[str, object],
    rows: Sequence[Mapping[str, object]],
) -> None:
    """Fail closed for every v2 governance event written by an arena."""
    governed_types = {
        "deposition_candidate_event",
        "deposition_confirmation_event",
        "anti_pattern_event",
    }
    if any(
        row.get("record_type") == "deposition_candidate_event"
        for row in rows
    ):
        governed_types.add("chain_deposition_event")
    expected_source = (
        manifest.get("dataset_source_sha256")
        or manifest.get("selected_cases_sha256")
    )
    for row in rows:
        if row.get("record_type") not in governed_types:
            continue
        if not isinstance(row.get("thresholds"), dict):
            raise ValueError(
                f"{artifact_path}: governed event missing thresholds"
            )
        try:
            int(row["seed"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"{artifact_path}: governed event missing seed"
            ) from exc
        if not _is_sha256(row.get("source_sha256")):
            raise ValueError(
                f"{artifact_path}: governed event has invalid source_sha256"
            )
        if row.get("source_sha256") != expected_source:
            raise ValueError(
                f"{artifact_path}: governed event source_sha256 mismatch"
            )
        if not _is_sha256(row.get("provenance_sha256")):
            raise ValueError(
                f"{artifact_path}: governed event has invalid provenance_sha256"
            )


def _signal_slices(
    rows: Sequence[Mapping[str, object]],
    keys: Sequence[str],
) -> list[dict[str, object]]:
    groups: dict[tuple[str, ...], list[Mapping[str, object]]] = {}
    for row in rows:
        key = tuple(str(row.get(name, "<missing>")) for name in keys)
        groups.setdefault(key, []).append(row)
    output = []
    for key, group in sorted(groups.items()):
        agreements = [
            bool(row["top1_agreement"])
            for row in group
            if row.get("top1_agreement") is not None
        ]
        null_rows = [
            row for row in group if row.get("failure_type") == "null"
        ]
        output.append(
            {
                **dict(zip(keys, key)),
                "case_count": len(group),
                "ranked_case_count": sum(
                    _finite(row.get("spearman_rho")) for row in group
                ),
                "mean_spearman_rho": _mean(
                    row.get("spearman_rho") for row in group
                ),
                "top1_agreement_rate": _rate(agreements),
                "abstention_rate": _rate(
                    bool(row.get("runtime_abstained")) for row in group
                ),
                "mean_oracle_rank_of_selected": _mean(
                    row.get("oracle_rank_of_selected") for row in group
                ),
                "mean_shadow_regret": _mean(
                    row.get("shadow_regret") for row in group
                ),
                "null_false_positive_rate": (
                    _rate(
                        bool(row.get("null_false_positive"))
                        for row in null_rows
                    )
                    if null_rows
                    else None
                ),
            }
        )
    return output


def _coordinate_slices(
    rows: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    expanded = []
    for dimension in (
        "age_sessions",
        "question_type",
        "evidence_condition",
    ):
        remapped = []
        for row in rows:
            coordinates = row.get("coordinates") or {}
            remapped.append(
                {
                    **row,
                    "dimension": dimension,
                    "dimension_value": (
                        coordinates.get(dimension)
                        if isinstance(coordinates, dict)
                        else None
                    ),
                }
            )
        expanded.extend(
            _signal_slices(
                remapped,
                ("arena_id", "dimension", "dimension_value"),
            )
        )
    return expanded


def _saturation_summary(
    rows: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    groups: dict[
        tuple[str, str, str, str],
        list[Mapping[str, object]],
    ] = {}
    for row in rows:
        arena_id = str(row.get("checkpoint", "")).split(":", 1)[0]
        key = (
            arena_id,
            str(row.get("failure_type", "<missing>")),
            str(row.get("subset", "<missing>")),
            str(row.get("runtime_branch", "<missing>")),
        )
        groups.setdefault(key, []).append(row)
    output = []
    for (arena_id, failure_type, subset, runtime_branch), group in sorted(groups.items()):
        covered = [row for row in group if bool(row.get("covered"))]
        output.append(
            {
                "arena_id": arena_id,
                "failure_type": failure_type,
                "subset": subset,
                "runtime_branch": runtime_branch,
                "case_count": len(group),
                "repair_effective_rate": _rate(
                    bool(row.get("repair_effective")) for row in group
                ),
                "cumulative_coverage_rate": _rate(
                    bool(row.get("covered")) for row in group
                ),
                "mean_cumulative_gain": _mean(
                    row.get("cumulative_gain") for row in group
                ),
                "mean_selected_skill_count": _mean(
                    len(row.get("selected_skill_ids") or ()) for row in group
                ),
                "mean_selected_skill_gain_on_covered": _mean(
                    row.get("mean_selected_gain") for row in covered
                ),
                "mean_shadow_regret": _mean(
                    row.get("shadow_regret") for row in group
                ),
                "shadow_selected_coverage_rate": _rate(
                    bool(row.get("shadow_selected_covered"))
                    for row in group
                    if row.get("shadow_selected_covered") is not None
                ),
                "shadow_oracle_coverage_rate": _rate(
                    bool(row.get("shadow_oracle_covered"))
                    for row in group
                    if row.get("shadow_oracle_covered") is not None
                ),
                "shadow_oracle_repair_effective_rate": _rate(
                    bool(row.get("shadow_oracle_repair_effective"))
                    for row in group
                    if row.get("shadow_oracle_repair_effective") is not None
                ),
            }
        )
    return output


def _skill_contribution(
    rows: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    groups: dict[
        tuple[str, str, str, str],
        dict[str, object],
    ] = {}
    for row in rows:
        arena_id = str(row.get("checkpoint", "")).split(":", 1)[0]
        failure_type = str(row.get("failure_type", "<missing>"))
        subset = str(row.get("subset", "<missing>"))
        selected = set(str(value) for value in row.get("selected_skill_ids") or ())
        gains = {
            str(skill_id): value
            for skill_id, value in row.get("gold_free_gains") or ()
        }
        for raw_skill_id in row.get("attempted_skill_ids") or ():
            skill_id = str(raw_skill_id)
            key = (arena_id, failure_type, subset, skill_id)
            bucket = groups.setdefault(
                key,
                {
                    "attempts": 0,
                    "selections": 0,
                    "covered_selections": 0,
                    "selected_gains": [],
                },
            )
            bucket["attempts"] = int(bucket["attempts"]) + 1
            if skill_id in selected:
                bucket["selections"] = int(bucket["selections"]) + 1
                if bool(row.get("covered")):
                    bucket["covered_selections"] = (
                        int(bucket["covered_selections"]) + 1
                    )
                if _finite(gains.get(skill_id)):
                    selected_gains = bucket["selected_gains"]
                    assert isinstance(selected_gains, list)
                    selected_gains.append(float(gains[skill_id]))
    output = []
    for key, bucket in sorted(groups.items()):
        arena_id, failure_type, subset, skill_id = key
        attempts = int(bucket["attempts"])
        selections = int(bucket["selections"])
        selected_gains = bucket["selected_gains"]
        assert isinstance(selected_gains, list)
        output.append(
            {
                "arena_id": arena_id,
                "failure_type": failure_type,
                "subset": subset,
                "skill_id": skill_id,
                "attempt_count": attempts,
                "selection_count": selections,
                "selection_rate": selections / attempts if attempts else None,
                "covered_selection_count": int(bucket["covered_selections"]),
                "mean_selected_gain": _mean(selected_gains),
                "cumulative_selected_gain": sum(selected_gains),
            }
        )
    return output


def _arm_comparison_summary(
    rows: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    groups: dict[tuple[str, str], list[Mapping[str, object]]] = {}
    for row in rows:
        if str(row.get("runtime_branch", "")) != "fix":
            continue
        groups.setdefault(
            (str(row.get("arena_id")), str(row.get("failure_type"))),
            [],
        ).append(row)
    return [
        _comparison_group_row(arena_id, failure_type, group)
        for (arena_id, failure_type), group in sorted(groups.items())
    ]


def _arm_comparison_by_budget(
    rows: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    groups: dict[tuple[str, str, int], list[Mapping[str, object]]] = {}
    for row in rows:
        if str(row.get("runtime_branch", "")) != "fix":
            continue
        try:
            budget = int(row.get("candidate_budget", 0))
        except (TypeError, ValueError):
            budget = 0
        groups.setdefault(
            (
                str(row.get("arena_id")),
                str(row.get("failure_type")),
                budget,
            ),
            [],
        ).append(row)
    return [
        {
            **_comparison_group_row(arena_id, failure_type, group),
            "candidate_budget": budget,
            "selection_is_nontrivial": budget >= 2,
        }
        for (arena_id, failure_type, budget), group in sorted(groups.items())
    ]


def _cmd_abstained(row: Mapping[str, object]) -> bool:
    return bool(row.get("cmd_abstained")) or (
        "cmd_abstained" not in row and row.get("cmd_selected_skill_id") is None
    )


def _control_abstained(row: Mapping[str, object]) -> bool:
    return bool(row.get("best_of_n_abstained")) or str(
        row.get("status", "")
    ) == "abstained_nonpositive_gain"


def _control_failed(row: Mapping[str, object]) -> bool:
    return (
        not _control_abstained(row)
        and (
            str(row.get("status", "ok")) != "ok"
            or not _finite(row.get("best_of_n_shadow_gold_gain"))
        )
    )


BEST_OF_N_GAIN_FIELD = "best_of_n_shadow_gold_gain"
CONTEXT_STUFFING_GAIN_FIELD = "context_stuffing_shadow_gold_gain"


def _paired_rows(
    group: Sequence[Mapping[str, object]],
    control_field: str = BEST_OF_N_GAIN_FIELD,
) -> list[Mapping[str, object]]:
    """Rows where both arms produced a measured, budget-aligned outcome.

    The significance table and the descriptive summary must agree on which
    pairs exist, so both read this one filter rather than each keeping a copy
    that could drift.

    Best-of-N is budget-matched to CMD and can abstain, so it carries the
    alignment and abstention checks. Context stuffing spends a fixed single
    call and never abstains, so for it a pair exists exactly when the arm
    produced a finite gain -- which is also what keeps rows from runs with the
    arm switched off out of the test.
    """
    if control_field == BEST_OF_N_GAIN_FIELD:
        return [
            row
            for row in group
            if bool(row.get("budget_aligned"))
            and not _cmd_abstained(row)
            and not _control_abstained(row)
            and not _control_failed(row)
            and _finite(row.get("cmd_shadow_gold_gain"))
            and _finite(row.get(control_field))
        ]
    return [
        row
        for row in group
        if not _cmd_abstained(row)
        and _finite(row.get("cmd_shadow_gold_gain"))
        and _finite(row.get(control_field))
    ]


#: Below this, a positive gold-free margin is a near-tie rather than genuine
#: confidence, and a confidence-versus-agreement gap says little.
NEGLIGIBLE_MARGIN = 1e-3


def _self_assessment_calibration(
    observations: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Self-confidence versus shadow agreement, per arena and failure type.

    The reported safety mismatch (`1.0` against `0.27`) is two different rates
    read as one: the runtime is confident on every case because the gold-free
    argmax has a positive margin, while the shadow judge prefers a different
    operator on most of them. Reporting only the gap invites reading it as a
    broken safety self-score, so the row also names the operator that won
    instead and how often the "confident" margin was negligible.
    """
    groups: dict[tuple[str, str], list[Mapping[str, object]]] = {}
    for row in observations:
        groups.setdefault(
            (str(row.get("arena_id")), str(row.get("failure_type"))),
            [],
        ).append(row)

    output: list[dict[str, object]] = []
    for (arena_id, failure_type), group in sorted(groups.items()):
        total = len(group)
        confident = [
            row
            for row in group
            if _finite(row.get("gold_free_margin"))
            and float(row["gold_free_margin"]) > 0
        ]
        agreed = [
            row
            for row in group
            if _finite(row.get("shadow_gold_margin"))
            and float(row["shadow_gold_margin"]) > 0
        ]
        tiny = [
            row
            for row in confident
            if float(row["gold_free_margin"]) < NEGLIGIBLE_MARGIN
        ]
        disagreements: dict[str, int] = {}
        for row in group:
            selected = row.get("selected_skill_id")
            oracle = row.get("oracle_skill_id")
            if oracle is None or oracle == selected:
                continue
            disagreements[str(oracle)] = disagreements.get(str(oracle), 0) + 1
        top_alternative, top_count = ("", 0)
        if disagreements:
            top_alternative, top_count = max(
                sorted(disagreements.items()),
                key=lambda item: item[1],
            )
        self_rate = len(confident) / total
        shadow_rate = len(agreed) / total
        low, high = wilson_interval(len(agreed), total)
        output.append(
            {
                "arena_id": arena_id,
                "failure_type": failure_type,
                "n": total,
                "self_confident_rate": self_rate,
                "shadow_agreement_rate": shadow_rate,
                "calibration_gap": self_rate - shadow_rate,
                "shadow_agreement_ci_low": low,
                "shadow_agreement_ci_high": high,
                "top1_agreement_rate": _rate_of(group, "top1_agreement"),
                "tiny_margin_share": (
                    len(tiny) / len(confident) if confident else 0.0
                ),
                "negligible_margin_threshold": NEGLIGIBLE_MARGIN,
                "top_alternative_skill_id": top_alternative,
                "top_alternative_share": top_count / total,
            }
        )
    return output


def _rate_of(group: Sequence[Mapping[str, object]], field: str) -> float:
    return (
        sum(1 for row in group if bool(row.get(field))) / len(group)
        if group
        else 0.0
    )


#: A gold-free gain this close to zero means the operator returned the context
#: unchanged. The repair actions are guarded -- ``_repair_granularity_context``
#: returns early when nothing in recall is coarse, and so on -- so an exactly
#: zero gain is a precondition that did not fire, not a repair that broke even.
INAPPLICABLE_GAIN_EPSILON = 1e-9


def _operator_applicability(
    observations: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """How often each operator was even applicable, per arena and failure type.

    Separates two things the argmax cannot tell apart: an operator that competed
    and lost, and an operator whose precondition never held on this layer so it
    returned the context untouched. Both look like a low score, but only the
    first is evidence about the operator's quality -- ranking the second is
    ranking a no-op, and when it wins the tie-break the system acts on a repair
    that did nothing.
    """
    groups: dict[tuple[str, str, str], list[float]] = {}
    for row in observations:
        for skill_id, gain in _score_mapping(
            row.get("gold_free_scores")
        ).items():
            if gain is None:
                continue
            groups.setdefault(
                (str(row.get("arena_id")), str(row.get("failure_type")), skill_id),
                [],
            ).append(gain)

    output: list[dict[str, object]] = []
    for (arena_id, failure_type, skill_id), gains in sorted(groups.items()):
        inapplicable = sum(
            1 for gain in gains if abs(gain) <= INAPPLICABLE_GAIN_EPSILON
        )
        output.append(
            {
                "arena_id": arena_id,
                "failure_type": failure_type,
                "skill_id": skill_id,
                "n": len(gains),
                "inapplicable_count": inapplicable,
                "inapplicable_rate": inapplicable / len(gains),
                # A layer-wide no-op is a structural fact about the operator's
                # guard, not a run-to-run outcome, so it is called out.
                "never_applicable": inapplicable == len(gains),
                "mean_gain": sum(gains) / len(gains),
                "epsilon": INAPPLICABLE_GAIN_EPSILON,
            }
        )
    return output


def _layer_effective_field(
    observations: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Candidates offered against candidates that could move the score.

    The argmax ranks every legal operator, but on each layer several of them are
    guarded off and return zero everywhere. The effective field is what remains,
    and it is the honest denominator for any claim that selection chose among N
    repairs.
    """
    applicability = _operator_applicability(observations)
    groups: dict[tuple[str, str], list[dict[str, object]]] = {}
    for row in applicability:
        groups.setdefault(
            (str(row["arena_id"]), str(row["failure_type"])),
            [],
        ).append(row)

    output: list[dict[str, object]] = []
    for (arena_id, failure_type), rows in sorted(groups.items()):
        never = [row for row in rows if row["never_applicable"]]
        output.append(
            {
                "arena_id": arena_id,
                "failure_type": failure_type,
                "candidates_offered": len(rows),
                "never_applicable_operators": len(never),
                "effective_field": len(rows) - len(never),
                "never_applicable_skill_ids": ";".join(
                    sorted(str(row["skill_id"]) for row in never)
                ),
            }
        )
    return output


#: Margin thresholds swept for the abstention curve. Fixed here rather than
#: taken from the data: choosing tau by looking at the same artifacts the curve
#: reports on would be selecting a threshold and scoring it on one sample.
ABSTENTION_THRESHOLDS = (0.0, 1e-4, 1e-3, 1e-2, 0.05, 0.1, 0.2)


def _abstention_curve_by_failure(
    observations: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Coverage against selective agreement as the margin threshold rises.

    Route A for the granularity finding: the gold-free margin on that layer is
    mostly a near-tie, so the question is whether declining to act on the
    low-margin cases buys agreement on the rest. Each row is one
    (arena, failure_type, tau) point, reusing the same ranking and curve code
    the identifiability report uses so the two cannot disagree.

    Only cases the runtime actually acted on are units here. A case it already
    abstained on cannot be credited to the threshold, and counting it would
    inflate the coverage the threshold appears to preserve.
    """
    groups: dict[tuple[str, str], list[CaseRankingInput]] = {}
    fields: dict[tuple[str, str], list[int]] = {}
    margins: dict[tuple[str, str], list[float]] = {}
    for row in observations:
        if bool(row.get("runtime_abstained")):
            continue
        gold_free = _score_mapping(row.get("gold_free_scores"))
        shadow = _score_mapping(row.get("shadow_gold_scores"))
        if not gold_free or set(gold_free) != set(shadow):
            continue
        # A no-op candidate sits at exactly zero on every case, so leaving it in
        # makes it the runner-up and reports a margin measured against a
        # candidate that could not have acted. The threshold has to be compared
        # against the gap among operators that can actually move the score.
        applicable = {
            skill_id: gain
            for skill_id, gain in gold_free.items()
            if gain is not None and abs(gain) > INAPPLICABLE_GAIN_EPSILON
        }
        key = (str(row.get("arena_id")), str(row.get("failure_type")))
        fields.setdefault(key, []).append(len(applicable))
        if len(applicable) >= 2:
            ordered = sorted(applicable.values(), reverse=True)
            margins.setdefault(key, []).append(ordered[0] - ordered[1])
        if len(applicable) < 2:
            continue
        groups.setdefault(key, []).append(
            case_ranking_from_mappings(
                case_id=str(row.get("case_id")),
                failure_type=str(row.get("failure_type")),
                gold_free_scores=applicable,
                shadow_gold_scores={
                    skill_id: shadow[skill_id] for skill_id in applicable
                },
            )
        )

    output: list[dict[str, object]] = []
    for (arena_id, failure_type), cases in sorted(groups.items()):
        _, report = analyze_gold_free_agreement(
            cases,
            abstention_thresholds=ABSTENTION_THRESHOLDS,
        )
        key = (arena_id, failure_type)
        field_sizes = fields.get(key, [])
        case_margins = margins.get(key, [])
        for point in report.abstention_curve:
            output.append(
                {
                    "arena_id": arena_id,
                    "failure_type": failure_type,
                    "threshold": point.threshold,
                    "eligible_cases": point.eligible_cases,
                    "retained_cases": point.retained_cases,
                    "coverage": point.coverage,
                    "agreements": point.agreements,
                    "selective_agreement": (
                        point.selective_agreement
                        if point.selective_agreement is not None
                        else ""
                    ),
                    "mean_retained_regret": (
                        point.mean_supervised_regret
                        if point.mean_supervised_regret is not None
                        else ""
                    ),
                    "baseline_agreement": (
                        report.overall_agreement
                        if report.overall_agreement is not None
                        else ""
                    ),
                    "mean_effective_field": (
                        round(sum(field_sizes) / len(field_sizes))
                        if field_sizes
                        else 0
                    ),
                    "mean_applicable_margin": (
                        sum(case_margins) / len(case_margins)
                        if case_margins
                        else ""
                    ),
                }
            )
    return output


def _score_mapping(value: object) -> dict[str, float | None]:
    """Candidate score pairs from an artifact row, as a mapping."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return {}
    result: dict[str, float | None] = {}
    for entry in value:
        if not isinstance(entry, Sequence) or len(entry) != 2:
            continue
        skill_id, gain = entry
        result[str(skill_id)] = (
            float(gain) if _finite(gain) else None
        )
    return result


def _null_protection_calibration(
    observations: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Abstention against what abstaining bought, per arena and failure type.

    Abstention means the opposite thing on the two kinds of case, so a single
    pooled rate is unreadable: on a `null` case there is no fault and holding
    back is the correct action, while on a faulted case it is a missed repair.
    Each row therefore declares its `case_kind` and is never averaged across
    the two.

    A false-positive count is also not yet a cost. Repairing a no-fault case is
    only harmful if it lost ground, so the harmful share is reported from
    `shadow_regret` alongside the raw false-positive rate -- the gap between the
    two is the share of interventions that were needless but benign.
    """
    groups: dict[tuple[str, str], list[Mapping[str, object]]] = {}
    for row in observations:
        groups.setdefault(
            (str(row.get("arena_id")), str(row.get("failure_type"))),
            [],
        ).append(row)

    output: list[dict[str, object]] = []
    for (arena_id, failure_type), group in sorted(groups.items()):
        total = len(group)
        acted = [row for row in group if not bool(row.get("runtime_abstained"))]
        abstained = total - len(acted)
        regrets = [
            float(row["shadow_regret"])
            for row in group
            if _finite(row.get("shadow_regret"))
        ]
        harmful = [
            row
            for row in acted
            if _finite(row.get("shadow_regret"))
            and float(row["shadow_regret"]) > 0
        ]
        low, high = wilson_interval(abstained, total)
        output.append(
            {
                "arena_id": arena_id,
                "failure_type": failure_type,
                # Named rather than inferred downstream: on `null` a high
                # abstention rate is the protection working, and on every other
                # stratum the same number is missed repairs.
                "case_kind": "no_fault" if failure_type == "null" else "faulted",
                "n": total,
                "abstention_rate": abstained / total if total else 0.0,
                "abstention_ci_low": low,
                "abstention_ci_high": high,
                "null_false_positive_rate": _rate_of(
                    group, "null_false_positive"
                ),
                "harmful_intervention_rate": (
                    len(harmful) / total if total else 0.0
                ),
                "harmful_share_of_acted": (
                    len(harmful) / len(acted) if acted else 0.0
                ),
                "mean_shadow_regret": (
                    sum(regrets) / len(regrets) if regrets else 0.0
                ),
            }
        )
    return output


def _case_families(
    observations: Sequence[Mapping[str, object]],
) -> dict[str, str]:
    """Case-to-family map, taken from the observation stream.

    The arm comparison events carry no ``family_id``, so the blocking unit has
    to come from the gold-free observations for the same cases.
    """
    return {
        str(row["case_id"]): str(row["family_id"])
        for row in observations
        if row.get("case_id") and row.get("family_id")
    }


def _arm_significance_summary(
    rows: Sequence[Mapping[str, object]],
    *,
    families: Mapping[str, str] | None = None,
    control_field: str = BEST_OF_N_GAIN_FIELD,
) -> list[dict[str, object]]:
    """Paired significance per arena x failure_type over the same pairs.

    A descriptive win count cannot say whether an arm is actually ahead, so
    each stratum also reports an exact sign test over discordant pairs and a
    bootstrap interval on the mean paired difference. When a case-to-family map
    is supplied the bootstrap resamples families, because sibling cases share
    an injected fault and resampling them independently understates the spread.
    """
    groups: dict[tuple[str, str], list[Mapping[str, object]]] = {}
    for row in rows:
        if str(row.get("runtime_branch", "")) != "fix":
            continue
        groups.setdefault(
            (str(row.get("arena_id")), str(row.get("failure_type"))),
            [],
        ).append(row)
    return [
        _significance_group_row(
            arena_id, failure_type, group, families, control_field
        )
        for (arena_id, failure_type), group in sorted(groups.items())
    ]


_CONTROL_ARM_NAMES = {
    BEST_OF_N_GAIN_FIELD: "best_of_n",
    CONTEXT_STUFFING_GAIN_FIELD: "context_stuffing",
}


def _significance_group_row(
    arena_id: str,
    failure_type: str,
    group: Sequence[Mapping[str, object]],
    families: Mapping[str, str] | None,
    control_field: str = BEST_OF_N_GAIN_FIELD,
) -> dict[str, object]:
    paired = _paired_rows(group, control_field)
    deltas = [
        float(row["cmd_shadow_gold_gain"]) - float(row[control_field])
        for row in paired
    ]
    cmd_wins = sum(1 for delta in deltas if delta > 0)
    control_wins = sum(1 for delta in deltas if delta < 0)

    block_ids: list[str] | None = None
    if families is not None:
        resolved = [families.get(str(row.get("case_id"))) for row in paired]
        # Blocking is only sound when every pair has a family; a partial map
        # would silently mix blocked and unblocked units in one interval.
        if resolved and all(family is not None for family in resolved):
            block_ids = [str(family) for family in resolved]

    diff, ci_low, ci_high = bootstrap_paired_diff(
        deltas, seed=BOOTSTRAP_SEED, families=block_ids
    )
    return {
        "arena_id": arena_id,
        "failure_type": failure_type,
        "control_arm": _CONTROL_ARM_NAMES.get(control_field, control_field),
        "n_paired": len(paired),
        "cmd_wins": cmd_wins,
        "control_wins": control_wins,
        "ties": len(deltas) - cmd_wins - control_wins,
        "mean_paired_diff": diff,
        "diff_ci_low": ci_low,
        "diff_ci_high": ci_high,
        "ci_excludes_zero": bool(ci_low > 0 or ci_high < 0),
        "sign_test_p": sign_test_p(deltas),
        "mcnemar_exact_p": mcnemar_exact_p(cmd_wins, control_wins),
        "significant_at_05": bool(sign_test_p(deltas) < 0.05),
        "bootstrap_unit": "family" if block_ids else "case",
        "n_families": len(set(block_ids)) if block_ids else None,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "bootstrap_iterations": BOOTSTRAP_ITERATIONS,
    }


def _comparison_group_row(
    arena_id: str,
    failure_type: str,
    group: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    cmd_abstained = _cmd_abstained
    control_abstained = _control_abstained
    control_failed = _control_failed
    paired = _paired_rows(group)
    cmd_wins = sum(
        float(row["cmd_shadow_gold_gain"])
        > float(row["best_of_n_shadow_gold_gain"])
        for row in paired
    )
    best_wins = sum(
        float(row["best_of_n_shadow_gold_gain"])
        > float(row["cmd_shadow_gold_gain"])
        for row in paired
    )
    budgets = [
        int(row.get("candidate_budget", 0))
        for row in group
        if str(row.get("candidate_budget", "")).lstrip("-").isdigit()
    ]
    return {
        "arena_id": arena_id,
        "failure_type": failure_type,
        "n_total": len(group),
        "n_paired": len(paired),
        "n_dropped_control_fail": sum(control_failed(row) for row in group),
        "n_cmd_abstain": sum(cmd_abstained(row) for row in group),
        "n_control_abstain": sum(control_abstained(row) for row in group),
        "n_dropped_budget_mismatch": sum(
            not bool(row.get("budget_aligned")) for row in group
        ),
        "n_dropped_cmd_shadow_fail": sum(
            not cmd_abstained(row)
            and not _finite(row.get("cmd_shadow_gold_gain"))
            for row in group
        ),
        "budget_aligned_count": sum(
            bool(row.get("budget_aligned")) for row in group
        ),
        "candidate_budget_min": min(budgets) if budgets else None,
        "candidate_budget_max": max(budgets) if budgets else None,
        "mean_candidate_budget": _mean(budgets),
        "n_budget_one": sum(value == 1 for value in budgets),
        "mean_cmd_shadow_gain": _mean(
            row.get("cmd_shadow_gold_gain") for row in paired
        ),
        "mean_best_of_n_shadow_gain": _mean(
            row.get("best_of_n_shadow_gold_gain") for row in paired
        ),
        "mean_structural_delta": _mean(
            float(row["cmd_shadow_gold_gain"])
            - float(row["best_of_n_shadow_gold_gain"])
            for row in paired
        ),
        "cmd_wins": cmd_wins,
        "best_of_n_wins": best_wins,
        "ties": len(paired) - cmd_wins - best_wins,
    }


def _ecology_tables(
    snapshots: Sequence[Mapping[str, object]],
) -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
]:
    niches: list[dict[str, object]] = []
    overlaps: list[dict[str, object]] = []
    succession: list[dict[str, object]] = []
    for snapshot in snapshots:
        arena_id = str(snapshot.get("checkpoint", "")).split(":", 1)[0]
        checkpoint = snapshot.get("checkpoint")
        event_count = snapshot.get("event_count")
        for niche in snapshot.get("niches", []):
            base = {
                "arena_id": arena_id,
                "checkpoint": checkpoint,
                "event_count": event_count,
                "skill_id": niche["skill_id"],
                "dominant_niche": niche.get("dominant_niche"),
                "specialization_index": niche.get("specialization_index"),
                "total_wins": niche.get("total_wins"),
                "total_attempts": niche.get("total_attempts"),
            }
            for failure_type, win_rate in niche.get("win_rates", []):
                niches.append(
                    {
                        **base,
                        "failure_type": failure_type,
                        "win_rate": win_rate,
                    }
                )
        for overlap in snapshot.get("overlaps", []):
            overlaps.append(
                {
                    "arena_id": arena_id,
                    "checkpoint": checkpoint,
                    **overlap,
                }
            )
        for skill_id, probability in snapshot.get("winner_distribution", []):
            succession.append(
                {
                    "arena_id": arena_id,
                    "checkpoint": checkpoint,
                    "event_count": event_count,
                    "skill_id": skill_id,
                    "winner_share": probability,
                    "diversity_index": snapshot.get("diversity_index"),
                    "jsd_from_previous": snapshot.get("jsd_from_previous"),
                }
            )
    return niches, overlaps, succession


def _cross_arena_niche_similarity(
    snapshots: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    final_by_arena: dict[str, Mapping[str, object]] = {}
    for row in snapshots:
        arena = str(row.get("checkpoint", "")).split(":", 1)[0]
        if (
            arena not in final_by_arena
            or int(row.get("event_count", 0))
            > int(final_by_arena[arena].get("event_count", 0))
        ):
            final_by_arena[arena] = row
    vectors: dict[tuple[str, str], dict[str, float]] = {}
    for arena, snapshot in final_by_arena.items():
        for niche in snapshot.get("niches", []):
            vectors[(arena, str(niche["skill_id"]))] = {
                str(failure): float(rate)
                for failure, rate in niche.get("win_rates", [])
            }
    output = []
    arenas = sorted(final_by_arena)
    skills = sorted({skill for _arena, skill in vectors})
    for left_index, left in enumerate(arenas):
        for right in arenas[left_index + 1 :]:
            for skill in skills:
                if (left, skill) not in vectors or (right, skill) not in vectors:
                    continue
                output.append(
                    {
                        "arena_a": left,
                        "arena_b": right,
                        "skill_id": skill,
                        "cosine_similarity": _cosine_mapping(
                            vectors[(left, skill)],
                            vectors[(right, skill)],
                        ),
                    }
                )
    return output


def _chain_spectrum(
    attempts: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    groups: dict[str, list[Mapping[str, object]]] = {}
    for row in attempts:
        groups.setdefault(str(row.get("arena_id")), []).append(row)
    output = []
    for arena, rows in sorted(groups.items()):
        counts = {
            "nonpositive": 0,
            "weak_positive": 0,
            "meaningful_positive": 0,
            "missing_or_nonfinite": 0,
        }
        for row in rows:
            value = row.get("chain_benefit")
            if not _finite(value):
                counts["missing_or_nonfinite"] += 1
            elif float(value) <= 0:
                counts["nonpositive"] += 1
            elif float(value) <= 0.05:
                counts["weak_positive"] += 1
            else:
                counts["meaningful_positive"] += 1
        output.append({"arena_id": arena, "attempt_count": len(rows), **counts})
    return output


def _directionality(
    attempts: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    groups: dict[tuple[str, str, str], dict[str, list[float]]] = {}
    for row in attempts:
        if not _finite(row.get("chain_benefit")):
            continue
        first = str(row["first_skill_id"])
        second = str(row["second_skill_id"])
        left, right = sorted((first, second))
        direction = "forward" if (first, second) == (left, right) else "reverse"
        groups.setdefault(
            (str(row["arena_id"]), left, right),
            {"forward": [], "reverse": []},
        )[direction].append(float(row["chain_benefit"]))
    output = []
    for (arena, left, right), values in sorted(groups.items()):
        forward = _mean(values["forward"])
        reverse = _mean(values["reverse"])
        output.append(
            {
                "arena_id": arena,
                "skill_a": left,
                "skill_b": right,
                "a_to_b_count": len(values["forward"]),
                "b_to_a_count": len(values["reverse"]),
                "mean_a_to_b_benefit": forward,
                "mean_b_to_a_benefit": reverse,
                "direction_delta": (
                    forward - reverse
                    if forward is not None and reverse is not None
                    else None
                ),
            }
        )
    return output


def _flatten_coactivation(
    snapshots: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    output = []
    for row in snapshots:
        for edge in row.get("edges", []):
            output.append(
                {
                    "arena_id": row.get("arena_id"),
                    "checkpoint": row.get("checkpoint"),
                    "observed_cases": row.get("observed_cases"),
                    **edge,
                }
            )
    return output


def _flatten_perturbations(
    rows: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    output = []
    for row in rows:
        base = {
            key: value
            for key, value in row.items()
            if key not in {"window_jsd", "record_type"}
        }
        windows = row.get("window_jsd") or ()
        if not windows:
            output.append(
                {
                    **base,
                    "window_end_position": None,
                    "window_jsd": None,
                }
            )
            continue
        for position, jsd in windows:
            output.append(
                {
                    **base,
                    "window_end_position": position,
                    "window_jsd": jsd,
                }
            )
    return output


def _write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        if fieldnames:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    return path


def _rate(values: Iterable[bool]) -> float | None:
    materialized = list(values)
    return (
        sum(bool(value) for value in materialized) / len(materialized)
        if materialized
        else None
    )


def _cosine_mapping(
    left: Mapping[str, float],
    right: Mapping[str, float],
) -> float:
    keys = sorted(set(left) | set(right))
    left_values = [float(left.get(key, 0.0)) for key in keys]
    right_values = [float(right.get(key, 0.0)) for key in keys]
    numerator = sum(a * b for a, b in zip(left_values, right_values))
    denominator = math.sqrt(sum(a * a for a in left_values)) * math.sqrt(
        sum(b * b for b in right_values)
    )
    return numerator / denominator if denominator else 0.0


if __name__ == "__main__":
    raise SystemExit(main())
