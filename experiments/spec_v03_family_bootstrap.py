"""Family-blocked paired bootstrap for Stage-5 posterior transfer effects."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import random
import sys
from typing import Mapping, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cmd_audit.spec_v03.order_only import (
    CaseOrderMetadata,
    compile_case_order_metadata,
    verify_split_case_ids,
)
from experiments.spec_v03_aggregate_transfer import arm_index, input_specs, validate


COMPARISONS = (
    ("matched", "reset"),
    ("global", "reset"),
    ("global_prefix", "global"),
    ("global_prefix", "matched"),
)


def _percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def bootstrap_family_means(
    values: Mapping[str, Sequence[float]], *, iterations: int, seed: int
) -> dict[str, object]:
    if iterations < 1 or not values:
        raise ValueError("bootstrap requires families and positive iterations")
    family_means = {
        family: sum(rows) / len(rows)
        for family, rows in values.items()
        if rows
    }
    families = tuple(sorted(family_means))
    if not families:
        raise ValueError("bootstrap has no non-empty family blocks")
    estimate = sum(family_means.values()) / len(family_means)
    rng = random.Random(seed)
    samples = []
    for _ in range(iterations):
        sample = [family_means[rng.choice(families)] for _ in families]
        samples.append(sum(sample) / len(sample))
    return {
        "family_count": len(families),
        "paired_event_count": sum(len(values[family]) for family in families),
        "family_macro_mean": estimate,
        "ci95": [_percentile(samples, 0.025), _percentile(samples, 0.975)],
        "positive_family_rate": sum(value > 0 for value in family_means.values()) / len(family_means),
        "tie_family_rate": sum(value == 0 for value in family_means.values()) / len(family_means),
    }


def _case_index(args: argparse.Namespace) -> dict[str, CaseOrderMetadata]:
    result: dict[str, CaseOrderMetadata] = {}
    for source in args.sources:
        rows = compile_case_order_metadata(
            source,
            group_a_root=args.group_a_root,
            limit=args.limit,
            case_seed=args.case_seed,
        )
        verify_split_case_ids(
            rows, args.data_root / f"{source}_stationary" / "split_manifest.json"
        )
        for row in rows:
            if row.case_id in result:
                raise ValueError("case identity collides across public sources")
            result[row.case_id] = row
    return result


def _paired_rows(runs: Sequence[Mapping[str, object]], case_index: Mapping[str, CaseOrderMetadata]):
    groups: dict[tuple[object, ...], list[Mapping[str, object]]] = defaultdict(list)
    for run in runs:
        identity = run["identity"]
        assert isinstance(identity, Mapping)
        groups[(identity["stream_id"], identity["order_manifest_sha256"], identity["model_id"], identity["seed"])].append(run)

    rows = []
    for group in groups.values():
        by_condition: dict[str, list[Mapping[str, object]]] = defaultdict(list)
        for run in group:
            by_condition[str(run["condition"])].append(run)
        for left_name, right_name in COMPARISONS:
            for left in by_condition[left_name]:
                for right in by_condition[right_name]:
                    left_index = arm_index(left, "mix_ghost")
                    right_index = arm_index(right, "mix_ghost")
                    for key in sorted(set(left_index) & set(right_index)):
                        case_id = key[0]
                        metadata = case_index.get(case_id)
                        if metadata is None:
                            raise ValueError(f"report case is absent from frozen family index: {case_id}")
                        identity = left["identity"]
                        assert isinstance(identity, Mapping)
                        rows.append({
                            "model_id": identity["model_id"],
                            "comparison": f"{left_name}>{right_name}",
                            "family_id": metadata.family_id,
                            "delta": float(left_index[key]["utility"]) - float(right_index[key]["utility"]),
                        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports-manifest", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--group-a-root", type=Path, default=Path("data/external/group_a"))
    parser.add_argument("--source", action="append", dest="sources", choices=("halumem", "memfail", "memtracebench"))
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--case-seed", type=int, default=20260827)
    parser.add_argument("--bootstrap-seed", type=int, default=20260901)
    parser.add_argument("--iterations", type=int, default=10000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.sources = tuple(args.sources or ("halumem", "memfail", "memtracebench"))

    case_index = _case_index(args)
    runs = [validate(path, condition, stream) for path, condition, stream in input_specs(args.reports_manifest, ())]
    paired = _paired_rows(runs, case_index)
    grouped: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in paired:
        grouped[(str(row["model_id"]), str(row["comparison"]))][str(row["family_id"])].append(float(row["delta"]))

    effects = []
    for offset, ((model, comparison), families) in enumerate(sorted(grouped.items())):
        effects.append({
            "model_id": model,
            "comparison": comparison,
            **bootstrap_family_means(families, iterations=args.iterations, seed=args.bootstrap_seed + offset),
        })

    models = sorted({row["model_id"] for row in effects})
    contrasts = []
    if len(models) == 2:
        qwen = next((model for model in models if "qwen" in model.casefold()), models[1])
        other = next(model for model in models if model != qwen)
        for offset, comparison in enumerate(sorted({row["comparison"] for row in effects})):
            left = grouped.get((qwen, comparison), {})
            right = grouped.get((other, comparison), {})
            common = sorted(set(left) & set(right))
            differences = {
                family: [sum(left[family]) / len(left[family]) - sum(right[family]) / len(right[family])]
                for family in common
            }
            if differences:
                contrasts.append({
                    "contrast": f"{qwen}-{other}",
                    "comparison": comparison,
                    **bootstrap_family_means(differences, iterations=args.iterations, seed=args.bootstrap_seed + 100 + offset),
                })

    result = {
        "schema_version": "cmd-spec-v03-family-blocked-bootstrap-v1",
        "block_unit": "family_id; all schedules, seeds, and interventions remain inside their family block",
        "iterations": args.iterations,
        "bootstrap_seed": args.bootstrap_seed,
        "case_index_count": len(case_index),
        "paired_row_count": len(paired),
        "effects": effects,
        "model_contrasts": contrasts,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"[RESULT] paired_rows={len(paired)}")
    print(f"[RESULT] effects={len(effects)}")
    print(f"[RESULT] output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
