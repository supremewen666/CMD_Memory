"""Audit frozen successor-v3 target-resolution observations from JSONL."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence

from cmd_audit.eval.successor_instrument_gates import (
    ActionabilityObservation,
    GateThresholds,
    evaluate_actionability_gate,
)

PROTOCOL_VERSION = "route-a-successor-semantic-actionability-v3"
MEASUREMENT_TYPE = "actionability_audit"


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("threshold manifest must be an object")
    return value


def _jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    result = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"JSONL row {number} is not an object")
            required = {
                "family_id", "expected_target_id", "predicted_target_id",
                "destructive_authorized", "ordering_state",
                "evidence_deployment_visible", "evidence_trusted",
            }
            if required - row.keys():
                raise ValueError(f"JSONL row {number} lacks explicit ordering evidence")
            result.append(row)
    if not result:
        raise ValueError("actionability JSONL is empty")
    return tuple(result)


def _seal(report: dict[str, Any]) -> None:
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    report["report_sha256"] = _hash(canonical)


def build_report(threshold_manifest: Path, observations_jsonl: Path) -> dict[str, Any]:
    manifest = _json(threshold_manifest)
    thresholds = GateThresholds.from_f1_manifest(manifest)
    rows = _jsonl(observations_jsonl)
    observations = tuple(ActionabilityObservation(**row) for row in rows)
    gate = evaluate_actionability_gate(observations, thresholds=thresholds)
    report: dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "measurement_type": MEASUREMENT_TYPE,
        "decision": "GO" if gate.passed else "REFUSE",
        "runtime_uses_gold": False,
        "model_calls": 0,
        "inputs": {"threshold_manifest_sha256": _hash(threshold_manifest.read_bytes()),
                   "observations_jsonl_sha256": _hash(observations_jsonl.read_bytes())},
        "thresholds": asdict(thresholds),
        "gate": asdict(gate),
    }
    _seal(report)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold-manifest", type=Path, required=True)
    parser.add_argument("--observations-jsonl", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = build_report(args.threshold_manifest, args.observations_jsonl)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        report = {"protocol_version": PROTOCOL_VERSION, "measurement_type": MEASUREMENT_TYPE,
                  "decision": "REFUSE", "failure": f"input_error:{type(error).__name__}"}
        _seal(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if report["decision"] == "GO" else 2


if __name__ == "__main__":
    raise SystemExit(main())
