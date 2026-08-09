"""Score a frozen successor-v3 relation calibration JSONL without calling a model."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence

from cmd_audit.eval.successor_instrument_gates import (
    GateThresholds,
    RelationObservation,
    evaluate_relation_gate,
)

PROTOCOL_VERSION = "route-a-successor-semantic-actionability-v3"
MEASUREMENT_TYPE = "relation_calibration"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("threshold manifest must be a JSON object")
    return value


def _read_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"JSONL row {number} is not an object")
        rows.append(value)
    if not rows:
        raise ValueError("calibration JSONL is empty")
    return tuple(rows)


def _report_hash(report: dict[str, Any]) -> str:
    payload = {key: value for key, value in report.items() if key != "report_sha256"}
    return _sha256_bytes(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())


def build_report(threshold_manifest: Path, observations_jsonl: Path) -> dict[str, Any]:
    manifest = _read_json(threshold_manifest)
    thresholds = GateThresholds.from_f1_manifest(manifest)
    rows = _read_jsonl(observations_jsonl)
    observations = tuple(RelationObservation(**row) for row in rows)
    decision = evaluate_relation_gate(observations, thresholds=thresholds)
    report: dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "measurement_type": MEASUREMENT_TYPE,
        "decision": "GO" if decision.passed else "REFUSE",
        "runtime_uses_gold": False,
        "model_calls": 0,
        "inputs": {
            "threshold_manifest_sha256": _sha256_bytes(threshold_manifest.read_bytes()),
            "observations_jsonl_sha256": _sha256_bytes(observations_jsonl.read_bytes()),
        },
        "thresholds": asdict(thresholds),
        "gate": asdict(decision),
    }
    report["report_sha256"] = _report_hash(report)
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
        report["report_sha256"] = _report_hash(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if report["decision"] == "GO" else 2


if __name__ == "__main__":
    raise SystemExit(main())
