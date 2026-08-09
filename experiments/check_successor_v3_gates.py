"""Fail-closed combiner for independently content-addressed successor-v3 audits."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from cmd_audit.eval.successor_protocol_freeze import require_validated_f1

PROTOCOL_VERSION = "route-a-successor-semantic-actionability-v3"
EXPECTED = {
    "relation": "relation_calibration",
    "actionability": "actionability_audit",
    "activity": "predicate_activity_audit",
    "shortcuts": "runtime_shortcut_audit",
}


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _report_hash(value: dict[str, Any]) -> str:
    return _hash(json.dumps({key: val for key, val in value.items() if key != "report_sha256"}, sort_keys=True, separators=(",", ":")).encode())


def build_report(
    threshold_manifest: Path,
    protocol_validation: Path,
    reports: dict[str, Path],
) -> dict[str, Any]:
    manifest = json.loads(threshold_manifest.read_text(encoding="utf-8"))
    validation = json.loads(protocol_validation.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(validation, dict):
        raise ValueError("manifest and validation must be JSON objects")
    protocol_manifest_hash = require_validated_f1(manifest, validation)
    threshold_hash = _hash(threshold_manifest.read_bytes())
    failures: list[str] = []
    input_hashes: dict[str, str] = {
        "threshold_manifest_sha256": threshold_hash,
        "protocol_manifest_sha256": protocol_manifest_hash,
        "protocol_validation_file_sha256": _hash(protocol_validation.read_bytes()),
    }
    for name, expected_type in EXPECTED.items():
        path = reports[name]
        raw = path.read_bytes()
        input_hashes[f"{name}_report_sha256"] = _hash(raw)
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            failures.append(f"{name}:not_object")
            continue
        if payload.get("protocol_version") != PROTOCOL_VERSION:
            failures.append(f"{name}:protocol_version")
        if payload.get("measurement_type") != expected_type:
            failures.append(f"{name}:measurement_type")
        if payload.get("report_sha256") != _report_hash(payload):
            failures.append(f"{name}:content_hash")
        if payload.get("inputs", {}).get("threshold_manifest_sha256") != threshold_hash:
            failures.append(f"{name}:threshold_binding")
        if payload.get("runtime_uses_gold") is not False or payload.get("model_calls") != 0:
            failures.append(f"{name}:runtime_or_model_contract")
        if payload.get("decision") != "GO":
            failures.append(f"{name}:gate_refused")
    report: dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "measurement_type": "successor_v3_gate_bundle",
        "decision": "GO" if not failures else "REFUSE",
        "headroom_authorized": not failures,
        "open_synthesis_authorized": False,
        "inputs": input_hashes,
        "failures": failures,
    }
    report["report_sha256"] = _report_hash(report)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold-manifest", type=Path, required=True)
    parser.add_argument("--protocol-validation", type=Path, required=True)
    for name in EXPECTED:
        parser.add_argument(f"--{name}-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    reports = {name: getattr(args, f"{name}_report") for name in EXPECTED}
    try:
        report = build_report(args.threshold_manifest, args.protocol_validation, reports)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        report = {"protocol_version": PROTOCOL_VERSION, "measurement_type": "successor_v3_gate_bundle", "decision": "REFUSE", "headroom_authorized": False, "open_synthesis_authorized": False, "failures": [f"input_error:{type(error).__name__}"]}
        report["report_sha256"] = _report_hash(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if report["decision"] == "GO" else 2


if __name__ == "__main__":
    raise SystemExit(main())
