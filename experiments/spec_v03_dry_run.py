"""No-model CLI for auditing and planning the spec-v0.3 experiment spine."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cmd_audit.spec_v03.adapters import iter_group_a_decision_views
from cmd_audit.spec_v03.run_manifest import RunManifest
from cmd_audit.spec_v03.source_audit import audit_downloads
from cmd_audit.spec_v03.splits import build_lockbox_manifest, build_split_manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group-a-root", type=Path, default=Path("data/external/group_a"))
    parser.add_argument("--group-b-root", type=Path, default=Path("data/external/group_b"))
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dataset", choices=("memfail", "halumem", "memtracebench"))
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20260826)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.audit and not args.dry_run:
        parser.error("one of --audit or --dry-run is required")
    audit = audit_downloads(args.group_a_root, args.group_b_root)
    payload: dict[str, object] = {"download_audit": audit.to_mapping()}
    exit_code = 0
    if args.dry_run:
        if args.dataset is None:
            parser.error("--dry-run requires --dataset")
        if args.limit <= 0:
            parser.error("--limit must be positive")
        cases = []
        for case in iter_group_a_decision_views(args.dataset, root=args.group_a_root, audit=audit):
            cases.append(case)
            if len(cases) >= args.limit:
                break
        split = build_split_manifest(cases, seed=args.seed)
        lockbox = build_lockbox_manifest(split)
        run = RunManifest.create(
            run_id=f"dry-run-{args.dataset}-{args.seed}", stage="dry_run", protocol_version="spec-v0.3",
            source_audit_sha256=audit.report_sha256, split_manifest_sha256=split.content_sha256,
            lockbox_manifest_sha256=lockbox.content_sha256, router_name="unconfigured",
            router_initial_state_sha256="0" * 64, model_id="no-model-dry-run",
            budget={"llm_calls": 0, "max_cases": len(cases)}, dry_run=True,
        )
        payload.update({"split_manifest": split.to_mapping(), "lockbox_manifest": lockbox.to_mapping(), "run_manifest": run.to_mapping()})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[RESULT] audit_sha256={audit.report_sha256}")
    print(f"[RESULT] output={args.output}")
    if args.dry_run:
        print("[RESULT] status=DRY_RUN_COMPLETE")
    else:
        print("[RESULT] status=AUDIT_COMPLETE")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
