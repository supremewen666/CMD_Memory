"""Validate the successor-v3 F0 dataset reservation and F1 freeze."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from cmd_audit.eval.successor_protocol_freeze import (
    VALIDATOR_VERSION,
    validate_protocol_freeze,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol-freeze", type=Path, required=True)
    parser.add_argument("--dataset-manifest", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--prompt-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        freeze = json.loads(args.protocol_freeze.read_text(encoding="utf-8"))
        if not isinstance(freeze, dict):
            raise ValueError("protocol freeze must be a JSON object")
        report = validate_protocol_freeze(
            freeze,
            dataset_path=args.dataset_manifest,
            repo_root=args.repo_root,
            prompt_path=args.prompt_file,
        ).as_dict()
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        report = {
            "valid": False,
            "reasons": [f"input_error:{type(error).__name__}"],
            "validator_version": VALIDATOR_VERSION,
            "manifest_sha256": "",
            "recomputed_hashes": {},
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"valid": report["valid"], "output": str(args.output)}))
    return 0 if report["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
