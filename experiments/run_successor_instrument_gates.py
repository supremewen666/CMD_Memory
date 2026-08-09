"""Materialize successor-v3's pre-headroom authorization decision.

This command consumes frozen observations.  It never calls a model and never
executes a candidate policy.  A GO permits a *new successor protocol* to measure
fixed-policy headroom; it does not revive predecessor Route A and does not
authorize open synthesis by itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from cmd_audit.eval.successor_instrument_gates import (
    ActionabilityObservation,
    GateThresholds,
    PredicateActivity,
    RelationObservation,
    ShortcutItem,
    audit_item_field_shortcuts,
    evaluate_actionability_gate,
    evaluate_predicate_activity_gate,
    evaluate_relation_gate,
)

PROTOCOL_VERSION = "route-a-successor-semantic-actionability-v3"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _contract_failures(thresholds: dict, observations: dict) -> tuple[str, ...]:
    failures: list[str] = []
    if thresholds.get("protocol_id") != PROTOCOL_VERSION:
        failures.append("threshold_protocol_version")
    if observations.get("protocol_version") != PROTOCOL_VERSION:
        failures.append("observation_protocol_version")
    if thresholds.get("freeze_stage") != "F1":
        failures.append("thresholds_not_f1")
    if observations.get("relation_instrument_frozen") is not True:
        failures.append("relation_instrument_not_frozen")
    if observations.get("runtime_uses_gold") is not False:
        failures.append("runtime_uses_gold")
    if observations.get("llm_calls_in_policy_search") != 0:
        failures.append("llm_calls_in_policy_search")
    return tuple(failures)


def build_report(threshold_manifest: Path, observations_path: Path) -> dict:
    registered = _load(threshold_manifest)
    observations = _load(observations_path)
    failures = _contract_failures(registered, observations)
    try:
        thresholds = GateThresholds.from_f1_manifest(registered)
    except (TypeError, ValueError) as error:
        failures += (f"invalid_thresholds:{type(error).__name__}",)
        raise ValueError("invalid F1 gate thresholds") from error

    relation = evaluate_relation_gate(
        tuple(RelationObservation(**row) for row in observations.get("relation_observations", ())),
        thresholds=thresholds,
    )
    actionability = evaluate_actionability_gate(
        tuple(
            ActionabilityObservation(**row)
            for row in observations.get("actionability_observations", ())
        ),
        thresholds=thresholds,
    )
    activity = evaluate_predicate_activity_gate(
        tuple(PredicateActivity(**row) for row in observations.get("predicate_activity", ())),
        thresholds=thresholds,
    )
    shortcuts = audit_item_field_shortcuts(
        tuple(ShortcutItem(**row) for row in observations.get("shortcut_items", ())),
        thresholds=thresholds,
    )
    passed = (
        not failures
        and relation.passed
        and actionability.passed
        and activity.passed
        and shortcuts.passed
    )
    return {
        "protocol_version": PROTOCOL_VERSION,
        "decision": "GO" if passed else "REFUSE",
        "headroom_authorized": passed,
        "open_synthesis_authorized": False,
        "contract_failures": list(failures),
        "inputs": {
            "threshold_manifest": str(threshold_manifest),
            "threshold_manifest_sha256": _sha256(threshold_manifest),
            "observations": str(observations_path),
            "observations_sha256": _sha256(observations_path),
        },
        "thresholds": asdict(thresholds),
        "relation_gate": asdict(relation),
        "actionability_gate": asdict(actionability),
        "predicate_activity_gate": asdict(activity),
        "shortcut_gate": asdict(shortcuts),
        "interpretation": (
            "GO authorizes fixed-policy successor headroom measurement only; "
            "the predecessor E0 STOP remains binding and open synthesis still "
            "requires a separately preregistered residual-headroom GO"
        ),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold-manifest", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = build_report(args.threshold_manifest, args.observations)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        report = {
            "protocol_version": PROTOCOL_VERSION,
            "decision": "REFUSE",
            "headroom_authorized": False,
            "open_synthesis_authorized": False,
            "contract_failures": [f"input_error:{type(error).__name__}"],
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"decision": report["decision"], "output": str(args.output)}))
    return 0 if report["decision"] == "GO" else 2


if __name__ == "__main__":
    raise SystemExit(main())
