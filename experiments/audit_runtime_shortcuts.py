"""Measure hidden-target alignment of exposed successor-v3 runtime fields."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence

from cmd_audit.eval.successor_instrument_gates import GateThresholds, ShortcutItem, audit_item_field_shortcuts

PROTOCOL_VERSION = "route-a-successor-semantic-actionability-v3"
MEASUREMENT_TYPE = "runtime_shortcut_audit"


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _rows(path: Path) -> tuple[dict[str, Any], ...]:
    result = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            row = json.loads(line)
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("fields"), dict)
                or not isinstance(row.get("permutation_predicted_target"), bool)
            ):
                raise ValueError(f"JSONL row {number} lacks explicit fields")
            result.append(row)
    if not result:
        raise ValueError("shortcut JSONL is empty")
    return tuple(result)


def build_report(threshold_manifest: Path, items_jsonl: Path) -> dict[str, Any]:
    manifest = json.loads(threshold_manifest.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("wrong protocol manifest")
    thresholds = GateThresholds.from_f1_manifest(manifest)
    items = tuple(ShortcutItem(**row) for row in _rows(items_jsonl))
    gate = audit_item_field_shortcuts(items, thresholds=thresholds)
    report: dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION, "measurement_type": MEASUREMENT_TYPE,
        "decision": "GO" if gate.passed else "REFUSE", "runtime_uses_gold": False, "model_calls": 0,
        "inputs": {"threshold_manifest_sha256": _hash(threshold_manifest.read_bytes()), "items_jsonl_sha256": _hash(items_jsonl.read_bytes())},
        "gate": asdict(gate),
    }
    report["report_sha256"] = _hash(json.dumps(report, sort_keys=True, separators=(",", ":")).encode())
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold-manifest", type=Path, required=True)
    parser.add_argument("--items-jsonl", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = build_report(args.threshold_manifest, args.items_jsonl)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        report = {"protocol_version": PROTOCOL_VERSION, "measurement_type": MEASUREMENT_TYPE, "decision": "REFUSE", "failure": f"input_error:{type(error).__name__}"}
        report["report_sha256"] = _hash(json.dumps(report, sort_keys=True, separators=(",", ":")).encode())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if report["decision"] == "GO" else 2


if __name__ == "__main__":
    raise SystemExit(main())
