#!/usr/bin/env python3
"""Family-blocked paired bootstrap for routing-mechanism ablations."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys
from typing import Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cmd_audit.spec_v03.order_only import (
    CaseOrderMetadata,
    compile_case_order_metadata,
    verify_split_case_ids,
)
from experiments.spec_v03_analyze_routing_ablation import (
    ARMS,
    COMPARISONS,
    _arm_events,
)
from experiments.spec_v03_family_bootstrap import bootstrap_family_means


def _load_case_indexes(paths: list[Path]) -> dict[str, CaseOrderMetadata]:
    result: dict[str, CaseOrderMetadata] = {}
    required = {
        "case_id",
        "family_id",
        "source_episode_id",
        "source_dataset_id",
        "incident_type",
    }
    for path in paths:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError(f"case index must contain a JSON list: {path}")
        for item in raw:
            if not isinstance(item, Mapping) or set(item) != required:
                raise ValueError(f"case index row has an invalid schema: {path}")
            row = CaseOrderMetadata(**{key: str(item[key]) for key in required})
            previous = result.get(row.case_id)
            if previous is not None and previous != row:
                raise ValueError("case index files disagree on case metadata")
            result[row.case_id] = row
    if not result:
        raise ValueError("case index files contain no rows")
    return result


def _pilot_case_seed(data_root: Path, source: str) -> int:
    manifest_path = data_root / f"{source}_stationary" / "pilot_manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    seed = raw.get("seed") if isinstance(raw, Mapping) else None
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError(f"pilot manifest has no integer seed: {manifest_path}")
    return seed


def _case_index(args: argparse.Namespace) -> dict[str, CaseOrderMetadata]:
    if args.case_indexes:
        return _load_case_indexes(args.case_indexes)
    result: dict[str, CaseOrderMetadata] = {}
    for source in args.sources:
        case_seed = (
            args.case_seed
            if args.case_seed is not None
            else _pilot_case_seed(args.data_root, source)
        )
        rows = compile_case_order_metadata(
            source,
            group_a_root=args.group_a_root,
            limit=args.limit,
            case_seed=case_seed,
        )
        try:
            verify_split_case_ids(
                rows,
                args.data_root / f"{source}_stationary" / "split_manifest.json",
            )
        except ValueError as error:
            raise ValueError(
                f"{source} case metadata does not match its pilot split at seed "
                f"{case_seed}; pass one or more frozen --case-index files"
            ) from error
        for row in rows:
            if row.case_id in result:
                raise ValueError("case identity collides across public sources")
            result[row.case_id] = row
    return result


def _paired_deltas(
    raw: Mapping[str, object],
    case_index: Mapping[str, CaseOrderMetadata],
    *,
    source: str,
) -> list[dict[str, object]]:
    stage5 = raw.get("results", {}).get("stage5")  # type: ignore[union-attr]
    if not isinstance(stage5, Mapping) or not isinstance(stage5.get("arms"), list):
        return []
    arm_index = {
        str(arm["arm"]): arm
        for arm in stage5["arms"]
        if isinstance(arm, Mapping) and arm.get("status") == "COMPLETE"
    }
    if not set(ARMS) <= set(arm_index):
        return []
    events = {arm: _arm_events(arm_index[arm]) for arm in ARMS}
    paired_keys = set.intersection(*(set(rows) for rows in events.values()))
    config = raw.get("config", {})
    model_id = config.get("model_id") if isinstance(config, Mapping) else None
    if not isinstance(model_id, str) or not model_id:
        raise ValueError(f"routing report lacks model identity: {source}")
    rows = []
    for left, right, mechanism in COMPARISONS:
        for case_id, event_index in sorted(paired_keys):
            metadata = case_index.get(case_id)
            if metadata is None:
                raise ValueError(f"routing report case is absent from family index: {case_id}")
            rows.append({
                "model_id": model_id,
                "mechanism": mechanism,
                "comparison": f"{left}>{right}",
                "family_id": metadata.family_id,
                "case_id": case_id,
                "event_index": event_index,
                "delta": (
                    float(events[left][(case_id, event_index)]["utility"])
                    - float(events[right][(case_id, event_index)]["utility"])
                ),
            })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--group-a-root", type=Path, default=Path("data/external/group_a"))
    parser.add_argument(
        "--source",
        action="append",
        dest="sources",
        choices=("halumem", "memfail", "memtracebench"),
    )
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--case-seed",
        type=int,
        help="Override the per-source seed recorded in pilot_manifest.json.",
    )
    parser.add_argument(
        "--case-index",
        action="append",
        dest="case_indexes",
        type=Path,
        help="Use a frozen case_index.json instead of reconstructing case identities.",
    )
    parser.add_argument("--bootstrap-seed", type=int, default=20260901)
    parser.add_argument("--iterations", type=int, default=10000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.sources = tuple(args.sources or ("halumem", "memfail", "memtracebench"))

    case_index = _case_index(args)
    paired = []
    report_count = 0
    for path in sorted(args.input_root.rglob("report.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            continue
        rows = _paired_deltas(raw, case_index, source=str(path))
        if rows:
            report_count += 1
            paired.extend(rows)
    if not paired:
        raise ValueError("no complete routing-ablation reports found")

    grouped: dict[tuple[str, str, str], dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in paired:
        grouped[(
            str(row["model_id"]),
            str(row["mechanism"]),
            str(row["comparison"]),
        )][str(row["family_id"])].append(float(row["delta"]))

    effects = []
    for offset, ((model, mechanism, comparison), families) in enumerate(sorted(grouped.items())):
        estimate = bootstrap_family_means(
            families,
            iterations=args.iterations,
            seed=args.bootstrap_seed + offset,
        )
        low, high = estimate["ci95"]
        effects.append({
            "model_id": model,
            "mechanism": mechanism,
            "comparison": comparison,
            **estimate,
            "direction": "POSITIVE" if low > 0 else "NEGATIVE" if high < 0 else "INCONCLUSIVE",
        })

    result = {
        "schema_version": "cmd-spec-v03-routing-family-bootstrap-v1",
        "block_unit": "family_id; schedules, seeds, and interventions remain inside each family block",
        "iterations": args.iterations,
        "bootstrap_seed": args.bootstrap_seed,
        "reports_observed": report_count,
        "case_index_count": len(case_index),
        "paired_row_count": len(paired),
        "effects": effects,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[RESULT] reports={report_count}")
    print(f"[RESULT] paired_rows={len(paired)}")
    print(f"[RESULT] effects={len(effects)}")
    print(f"[RESULT] output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
