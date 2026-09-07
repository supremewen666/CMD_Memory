"""Filter a discovered library to revisions produced by frozen source splits."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cmd_audit.spec_v03.contracts import canonical_sha256
from cmd_audit.spec_v03.family_disjoint import load_split_assignments
from cmd_audit.spec_v03.runtime_bundle import load_runtime_cases
from cmd_audit.spec_v03.skill_discovery_provider import load_skill_library, serialize_skill_library
from cmd_audit.spec_v03.splits import SPLITS


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill-library", type=Path, required=True)
    parser.add_argument("--runtime-cases", type=Path, action="append", required=True)
    parser.add_argument("--split-manifest", type=Path, action="append", required=True)
    parser.add_argument("--include-split", action="append", choices=SPLITS, default=None)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit-output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if len(args.runtime_cases) != len(args.split_manifest):
        raise ValueError("repeat --runtime-cases and --split-manifest the same number of times")
    included_splits = tuple(args.include_split or ("D_skill",))
    allowed_failures: set[str] = set()
    source_families: set[str] = set()
    target_families: set[str] = set()
    source_case_count = 0
    for runtime_path, split_path in zip(args.runtime_cases, args.split_manifest):
        bundles = load_runtime_cases(runtime_path)
        assignments = load_split_assignments(split_path)
        if set(assignments) != {bundle.case_id for bundle in bundles}:
            raise ValueError("split manifest and runtime cases must contain the same case IDs")
        for bundle in bundles:
            split = assignments[bundle.case_id]
            if split in included_splits:
                source_case_count += 1
                source_families.add(bundle.family_id)
                allowed_failures.add(
                    "stage6-" + canonical_sha256({"case_id": bundle.case_id, "root": bundle.memory_state.root})
                )
            elif split in {"T_online", "T_anchor", "T_final"}:
                target_families.add(bundle.family_id)
    overlap = sorted(source_families & target_families)
    if overlap:
        raise ValueError("source skill and target families overlap")

    loaded = load_skill_library(args.skill_library)
    selected = tuple(
        skill for skill in loaded.skills if skill.producing_failure_id in allowed_failures
    )
    if not selected:
        raise ValueError("source split did not retain any discovered skills")
    library = serialize_skill_library(selected)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.audit_output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(library, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.audit_output.write_text(json.dumps({
        "schema_version": "cmd-spec-v03-source-skill-filter-audit-v1",
        "included_splits": list(included_splits),
        "input_revision_count": len(loaded.skills),
        "output_revision_count": len(selected),
        "source_case_count": source_case_count,
        "source_family_count": len(source_families),
        "target_family_count": len(target_families),
        "source_target_family_overlap_count": 0,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[RESULT] revisions={len(selected)}")
    print("[RESULT] source_target_family_overlap=0")
    print(f"[RESULT] output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
