"""Build the offline CMD spec v0.3 multi-model experiment manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cmd_audit.spec_v03.experiment_matrix import (
    Budget,
    DataEntitlement,
    ModelArm,
    SystemArm,
    build_experiment_matrix,
    freeze_manifest,
)


def _load_pins_config(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read pins config: {exc}") from exc
    if not isinstance(value, dict) or set(value) != {"budget", "data_entitlement", "models", "systems"}:
        raise ValueError("pins config requires exactly budget, data_entitlement, models, and systems")
    if not isinstance(value["budget"], dict) or not isinstance(value["data_entitlement"], dict):
        raise ValueError("pins config budget and data_entitlement must be mappings")
    if not isinstance(value["models"], list) or not isinstance(value["systems"], list):
        raise ValueError("pins config models and systems must be lists")
    return {
        "budget": Budget(**value["budget"]),
        "data_entitlement": DataEntitlement(**value["data_entitlement"]),
        "models": tuple(ModelArm(**item) for item in value["models"]),
        "systems": tuple(SystemArm(**item) for item in value["systems"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", action="append", dest="families", default=[])
    parser.add_argument("--seed", type=int, default=20260827)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--confirmatory", action="store_true")
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--pins-config", type=Path)
    args = parser.parse_args(argv)
    confirmatory = args.confirmatory or args.freeze
    if confirmatory and args.pins_config is None:
        parser.error("--confirmatory/--freeze requires --pins-config with complete exact pins")
    try:
        pins = _load_pins_config(args.pins_config) if args.pins_config else {}
        manifest = build_experiment_matrix(
            family_ids=args.families or ("default-family",), base_seed=args.seed,
            confirmatory=confirmatory, **pins,
        )
        if args.freeze:
            manifest = freeze_manifest(manifest)
    except ValueError as exc:
        parser.error(str(exc))
    rendered = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.write_text(rendered, encoding="utf-8")
        print(f"[RESULT] status={manifest['status']}")
        if "frozen_sha256" in manifest:
            print(f"[RESULT] frozen_sha256={manifest['frozen_sha256']}")
        print(f"[RESULT] output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
