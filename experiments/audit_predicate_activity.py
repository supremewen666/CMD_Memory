"""Aggregate frozen runtime predicate events; it never evaluates a policy."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence

from cmd_audit.eval.successor_instrument_gates import (
    REGISTERED_PREDICATES,
    GateThresholds,
    PredicateActivity,
    evaluate_predicate_activity_gate,
)

PROTOCOL_VERSION = "route-a-successor-semantic-actionability-v3"
MEASUREMENT_TYPE = "predicate_activity_audit"


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load_manifest(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("threshold manifest must be an object")
    return value


def _events(path: Path) -> tuple[dict[str, Any], ...]:
    result = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        event = json.loads(line)
        if not isinstance(event, dict):
            raise ValueError(f"JSONL row {number} is not an object")
        required = {
            "predicate",
            "case_id",
            "family_id",
            "fired",
            "action_compatible",
            "null_case",
        }
        if set(event) != required:
            raise ValueError(f"JSONL row {number} has a non-closed activity schema")
        if event.get("predicate") not in REGISTERED_PREDICATES:
            raise ValueError(f"JSONL row {number} has an unregistered predicate")
        if not all(
            isinstance(event.get(name), str) and event[name]
            for name in ("case_id", "family_id")
        ):
            raise ValueError(f"JSONL row {number} lacks case_id/family_id")
        if not all(isinstance(event.get(name), bool) for name in ("fired", "action_compatible", "null_case")):
            raise ValueError(f"JSONL row {number} has non-boolean activity flags")
        result.append(event)
    if not result:
        raise ValueError("predicate activity JSONL is empty")
    return tuple(result)


def build_report(threshold_manifest: Path, events_jsonl: Path) -> dict[str, Any]:
    manifest = _load_manifest(threshold_manifest)
    thresholds = GateThresholds.from_f1_manifest(manifest)
    events = _events(events_jsonl)
    activities: list[PredicateActivity] = []
    for name in sorted(REGISTERED_PREDICATES):
        compatible_fires = [
            event
            for event in events
            if event["predicate"] == name
            and event["fired"]
            and event["action_compatible"]
        ]
        activities.append(
            PredicateActivity(
                predicate=name,
                fires=len(compatible_fires),
                families=len({event["family_id"] for event in compatible_fires}),
                null_case_fires=sum(
                    event["predicate"] == name
                    and event["fired"]
                    and event["null_case"]
                    for event in events
                ),
                null_cases=sum(
                    event["predicate"] == name and event["null_case"]
                    for event in events
                ),
            )
        )
    activities_tuple = tuple(activities)
    gate = evaluate_predicate_activity_gate(activities_tuple, thresholds=thresholds)
    report: dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION, "measurement_type": MEASUREMENT_TYPE,
        "decision": "GO" if gate.passed else "REFUSE", "runtime_uses_gold": False, "model_calls": 0,
        "inputs": {"threshold_manifest_sha256": _hash(threshold_manifest.read_bytes()), "events_jsonl_sha256": _hash(events_jsonl.read_bytes())},
        "activities": [asdict(row) for row in activities_tuple], "gate": asdict(gate),
    }
    report["report_sha256"] = _hash(json.dumps(report, sort_keys=True, separators=(",", ":")).encode())
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold-manifest", type=Path, required=True)
    parser.add_argument("--events-jsonl", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = build_report(args.threshold_manifest, args.events_jsonl)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        report = {"protocol_version": PROTOCOL_VERSION, "measurement_type": MEASUREMENT_TYPE, "decision": "REFUSE", "failure": f"input_error:{type(error).__name__}"}
        report["report_sha256"] = _hash(json.dumps(report, sort_keys=True, separators=(",", ":")).encode())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if report["decision"] == "GO" else 2


if __name__ == "__main__":
    raise SystemExit(main())
