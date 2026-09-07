#!/usr/bin/env python3
"""Export gold-free CMD cases for MemSkill or ERSkill evidence generation."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cmd_audit.spec_v03.family_disjoint import select_runtime_splits
from cmd_audit.spec_v03.prequential_executor import RuntimeOrderManifest
from cmd_audit.spec_v03.runtime_bundle import load_runtime_cases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-cases", type=Path, required=True)
    parser.add_argument("--event-order", type=Path, required=True)
    parser.add_argument("--split-manifest", type=Path, required=True)
    parser.add_argument(
        "--include-split", action="append", required=True,
        choices=("D_skill", "D_router", "D_cal", "D_lifecycle", "T_online", "T_anchor", "T_final"),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    bundles = load_runtime_cases(args.runtime_cases)
    order_raw = json.loads(args.event_order.read_text(encoding="utf-8"))
    if not isinstance(order_raw, dict):
        raise ValueError("event order must contain one JSON object")
    order = RuntimeOrderManifest.from_mapping(order_raw)
    bundles, selected_order, audit = select_runtime_splits(
        bundles, order, args.split_manifest, tuple(args.include_split),
    )
    by_id = {bundle.case_id: bundle for bundle in bundles}
    records = []
    for row in selected_order.rows:
        bundle = by_id[row.case_id]
        decision = replace(bundle.decision_view, event_index=row.event_index)
        records.append({
            "schema_version": "cmd-skill-competitor-input-v1",
            "case_id": bundle.case_id,
            "family_id": bundle.family_id,
            "event_index": row.event_index,
            "decision_view": json.loads(json.dumps(decision.to_mapping(), sort_keys=True)),
        })
    payload = {
        "schema_version": "cmd-skill-competitor-input-manifest-v1",
        "split_audit": audit,
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, args.output)
    print(f"[RESULT] records={len(records)}")
    print(f"[RESULT] output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
