#!/usr/bin/env python3
"""Replay frozen model choices through Stage 7 and score with a sealed sidecar."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cmd_audit.spec_v03.family_disjoint import select_runtime_splits
from cmd_audit.spec_v03.contracts import canonical_sha256
from cmd_audit.spec_v03.prequential_executor import RuntimeOrderManifest
from cmd_audit.spec_v03.runtime_bundle import load_runtime_cases
from cmd_audit.spec_v03.runtime_pipeline import RuntimePipeline
from cmd_audit.spec_v03.sealed_governance import (
    FrozenSelectionProposalProvider,
    execute_governance_replay,
    frozen_operators_from_stage5_report,
    load_sealed_cases,
    score_governance_records,
)
from cmd_audit.spec_v03.skill_discovery_provider import load_skill_library


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-cases", type=Path, required=True)
    parser.add_argument("--event-order", type=Path, required=True)
    parser.add_argument("--split-manifest", type=Path, required=True)
    parser.add_argument("--include-split", action="append", default=None)
    parser.add_argument("--sealed-sidecar", type=Path, required=True)
    parser.add_argument("--selection-report", type=Path, required=True)
    parser.add_argument("--skill-library", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    bundles = load_runtime_cases(args.runtime_cases)
    raw_order = json.loads(args.event_order.read_text(encoding="utf-8"))
    if not isinstance(raw_order, dict):
        raise ValueError("event order must contain one JSON object")
    order = RuntimeOrderManifest.from_mapping(raw_order)
    bundles, order, split_audit = select_runtime_splits(
        bundles, order, args.split_manifest,
        tuple(args.include_split or ("T_anchor", "T_final")),
    )
    loaded = load_skill_library(args.skill_library).skills
    library = tuple({
        skill.skill_revision_id: skill
        for skill in (*RuntimePipeline().frozen_skill_library, *loaded)
    }.values())
    operators = {
        skill.skill_revision_id: str(skill.program["operator_id"])
        for skill in library
    }
    frozen = frozen_operators_from_stage5_report(args.selection_report, operators)
    provider = FrozenSelectionProposalProvider(frozen)
    sealed = load_sealed_cases(args.sealed_sidecar)
    records = execute_governance_replay(
        bundles, order, provider, sealed, run_id=args.run_id,
    )
    result = score_governance_records(
        records, sealed, {bundle.case_id: bundle.family_id for bundle in bundles},
    )
    result["config"] = {
        "run_id": args.run_id,
        "selection_report": str(args.selection_report),
        "included_splits": list(args.include_split or ("T_anchor", "T_final")),
        "model_calls": 0,
        "selection_source": "frozen_stage5_mix_ghost",
    }
    result["split_audit"] = split_audit
    result.pop("report_sha256", None)
    result["report_sha256"] = canonical_sha256(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[RESULT] cases={len(bundles)}")
    print(f"[RESULT] governance_records={len(records)}")
    print("[RESULT] model_calls=0")
    print(f"[RESULT] output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
