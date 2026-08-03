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
    depositions = records.get("chain_deposition_event", [])
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
            output / "perturbation_response.csv",
            _flatten_perturbations(perturbations),
        )
    )

    summary = {
        "analysis_kind": "descriptive_observational",
        "hypothesis_tests_run": False,
        "arena_count": len(manifests),
        "case_observations": len(observations),
        "saturation_events": len(saturation),
        "arm_comparison_events": len(arm_comparisons),
        "ecology_snapshots": len(snapshots),
        "chain_attempts": len(attempts),
        "deposition_events": len(depositions),
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
    print("[RESULT] hypothesis_tests_run=0")
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


def _comparison_group_row(
    arena_id: str,
    failure_type: str,
    group: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    def cmd_abstained(row: Mapping[str, object]) -> bool:
        return bool(row.get("cmd_abstained")) or (
            "cmd_abstained" not in row
            and row.get("cmd_selected_skill_id") is None
        )

    def control_abstained(row: Mapping[str, object]) -> bool:
        return bool(row.get("best_of_n_abstained")) or str(
            row.get("status", "")
        ) == "abstained_nonpositive_gain"

    def control_failed(row: Mapping[str, object]) -> bool:
        return (
            not control_abstained(row)
            and (
                str(row.get("status", "ok")) != "ok"
                or not _finite(row.get("best_of_n_shadow_gold_gain"))
            )
        )

    paired = [
        row
        for row in group
        if bool(row.get("budget_aligned"))
        and not cmd_abstained(row)
        and not control_abstained(row)
        and not control_failed(row)
        and _finite(row.get("cmd_shadow_gold_gain"))
        and _finite(row.get("best_of_n_shadow_gold_gain"))
    ]
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
