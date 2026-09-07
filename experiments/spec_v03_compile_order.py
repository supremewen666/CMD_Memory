"""Compile a lightweight phase-labelled order over existing runtime cases."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cmd_audit.spec_v03.order_only import (
    compile_case_order_metadata,
    compile_phase_labelled_recurring_order,
    verify_split_case_ids,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("halumem", "memfail", "memtracebench"), required=True)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--case-seed", type=int, default=20260827)
    parser.add_argument("--order-seed", type=int, required=True)
    parser.add_argument("--group-a-root", type=Path, default=Path("data/external/group_a"))
    parser.add_argument("--split-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case-index-output", type=Path)
    args = parser.parse_args()

    rows = compile_case_order_metadata(
        args.source, group_a_root=args.group_a_root, limit=args.limit, case_seed=args.case_seed
    )
    verify_split_case_ids(rows, args.split_manifest)
    order = compile_phase_labelled_recurring_order(rows, seed=args.order_seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(order.to_mapping(), indent=2, sort_keys=True) + "\n")
    if args.case_index_output is not None:
        args.case_index_output.parent.mkdir(parents=True, exist_ok=True)
        args.case_index_output.write_text(
            json.dumps([row.to_mapping() for row in rows], indent=2, sort_keys=True) + "\n"
        )
    print(f"[RESULT] cases={len(rows)}")
    print(f"[RESULT] phases={sorted({row.regime for row in order.rows})}")
    print(f"[RESULT] output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
