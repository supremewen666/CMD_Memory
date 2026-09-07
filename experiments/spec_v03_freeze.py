"""Compile an auditable, fail-closed CMD-RepairStream F-DATA bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cmd_audit.spec_v03.freeze import FreezeConfig, FreezeError, compile_freeze_bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="JSON source/quota/order configuration")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--group-a-root", type=Path, default=Path("data/external/group_a"))
    parser.add_argument("--group-b-root", type=Path, default=Path("data/external/group_b"))
    parser.add_argument("--freeze-id", help="Explicit immutable freeze identifier")
    parser.add_argument("--acknowledge-lockbox", action="store_true", help="Explicitly acknowledge T_anchor/T_final lockbox handling")
    parser.add_argument("--sealed-output-dir", type=Path, help="Separate sealed/lockbox destination for F_DATA_FROZEN")
    args = parser.parse_args(argv)
    try:
        raw = json.loads(args.config.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise FreezeError("config JSON must be an object")
        manifest = compile_freeze_bundle(
            FreezeConfig.from_mapping(raw), output_dir=args.output_dir,
            group_a_root=args.group_a_root, group_b_root=args.group_b_root,
            freeze_id=args.freeze_id, acknowledge_lockbox=args.acknowledge_lockbox,
            sealed_output_dir=args.sealed_output_dir,
        )
    except (OSError, json.JSONDecodeError, FreezeError) as exc:
        parser.error(str(exc))
    print(f"[RESULT] status={manifest['status']}")
    print(f"[RESULT] non_confirmatory={manifest['confirmation']['non_confirmatory']}")
    print(f"[RESULT] cases={manifest['case_count']}")
    print(f"[RESULT] output={args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
