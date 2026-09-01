#!/usr/bin/env python3
"""Paired family-blocked analysis of source-discovered skill transfer."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys
from typing import Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cmd_audit.spec_v03.order_only import compile_case_order_metadata, verify_split_case_ids
from experiments.spec_v03_analyze_routing_ablation import _arm_events
from experiments.spec_v03_family_bootstrap import bootstrap_family_means


def _mix_events(path: Path) -> tuple[str, dict[tuple[str, int], dict[str, object]]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    config = raw.get("config") if isinstance(raw, Mapping) else None
    stage5 = raw.get("results", {}).get("stage5") if isinstance(raw, Mapping) else None
    arms = stage5.get("arms") if isinstance(stage5, Mapping) else None
    if not isinstance(config, Mapping) or not isinstance(config.get("model_id"), str) or not isinstance(arms, list):
        raise ValueError(f"invalid family-disjoint report: {path}")
    arm = next(
        (row for row in arms if isinstance(row, Mapping) and row.get("arm") == "mix_ghost" and row.get("status") == "COMPLETE"),
        None,
    )
    if not isinstance(arm, Mapping):
        raise ValueError(f"family-disjoint report lacks complete Mix GHOST arm: {path}")
    return str(config["model_id"]), _arm_events(arm)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--group-a-root", type=Path, default=Path("data/external/group_a"))
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--case-seed", type=int, default=20260827)
    parser.add_argument("--iterations", type=int, default=10000)
    parser.add_argument("--bootstrap-seed", type=int, default=20260901)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    case_index = {}
    for source in ("halumem", "memfail", "memtracebench"):
        rows = compile_case_order_metadata(
            source, group_a_root=args.group_a_root,
            limit=args.limit, case_seed=args.case_seed,
        )
        verify_split_case_ids(rows, args.data_root / f"{source}_stationary" / "split_manifest.json")
        case_index.update({row.case_id: row for row in rows})

    family_deltas: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    streams = []
    for model_dir in sorted(path for path in args.input_root.iterdir() if path.is_dir()):
        for stream_dir in sorted(path for path in model_dir.iterdir() if path.is_dir()):
            base_path = stream_dir / "base_reset" / "report.json"
            skills_path = stream_dir / "skills_reset" / "report.json"
            if not base_path.is_file() or not skills_path.is_file():
                continue
            base_model, base = _mix_events(base_path)
            skills_model, skills = _mix_events(skills_path)
            if base_model != skills_model:
                raise ValueError("paired reports disagree on model identity")
            paired = sorted(set(base) & set(skills))
            if not paired:
                raise ValueError("family-disjoint pair has no settled events")
            deltas = []
            different = 0
            for key in paired:
                metadata = case_index.get(key[0])
                if metadata is None:
                    raise ValueError(f"family-disjoint case missing from index: {key[0]}")
                delta = float(skills[key]["utility"]) - float(base[key]["utility"])
                deltas.append(delta)
                family_deltas[base_model][metadata.family_id].append(delta)
                different += (
                    skills[key]["selected_skill_revision_id"]
                    != base[key]["selected_skill_revision_id"]
                )
            streams.append({
                "model_id": base_model,
                "stream_id": stream_dir.name,
                "paired_event_count": len(paired),
                "mean_delta_skills_over_base": sum(deltas) / len(deltas),
                "different_choice_rate": different / len(paired),
            })

    effects = []
    for offset, (model, families) in enumerate(sorted(family_deltas.items())):
        estimate = bootstrap_family_means(
            families, iterations=args.iterations, seed=args.bootstrap_seed + offset,
        )
        low, high = estimate["ci95"]
        effects.append({
            "model_id": model,
            "comparison": "skills_reset>base_reset",
            **estimate,
            "direction": "POSITIVE" if low > 0 else "NEGATIVE" if high < 0 else "INCONCLUSIVE",
        })
    if len(streams) != 12 or len(effects) != 2:
        raise ValueError(f"expected 12 paired streams and two model effects, got {len(streams)} and {len(effects)}")
    result = {
        "schema_version": "cmd-spec-v03-family-disjoint-analysis-v1",
        "reports_observed": 24,
        "paired_stream_count": len(streams),
        "block_unit": "family_id; schedules remain inside each family block",
        "stream_effects": streams,
        "family_bootstrap_effects": effects,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[RESULT] paired_streams={len(streams)}")
    print(f"[RESULT] effects={len(effects)}")
    print(f"[RESULT] output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
