"""P4C-3 native, gold-free syndrome detection and sealed post-runtime audit."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from typing import Mapping

from cmd_audit.core.state_codec import append_jsonl_fsync, atomic_json_write, content_sha256
from cmd_audit.repair.ecc import MemAuditEccAdapter


VISIBLE_SCHEMA = "cmd-p4c3-visible-telemetry-v1"
RUNTIME_SCHEMA = "cmd-p4c3-native-detection-runtime-v1"
AUDIT_SCHEMA = "cmd-p4c3-native-detection-audit-v1"
MECHANISMS = ("process_fault", "state_drift", "adversarial_poison")
LABELS = (*MECHANISMS, "no_fault")
_VISIBLE_FIELDS = {
    "schema_version",
    "case_id",
    "observed_at_event_index",
    "state_root",
    "source_manifest_root",
    "pipeline_checks",
    "active_versions",
    "integrity_signals",
}
_PIPELINE_FIELDS = {"retrieval", "injection", "granularity", "safety"}
_VERSION_FIELDS = {"slot", "memory_id", "observed_at"}
_INTEGRITY_FIELDS = {
    "memory_id",
    "cas_valid",
    "influence_score",
    "influence_threshold",
}
_FORBIDDEN = ("gold", "label", "answer", "oracle", "sidecar", "replay")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} is required")
    return value


def _reject_sealed_evidence(value: object, path: str = "visible_telemetry") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key).casefold()
            if any(marker in key_text for marker in _FORBIDDEN):
                raise ValueError(f"gold-free runtime rejects {path}.{key}")
            _reject_sealed_evidence(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_sealed_evidence(nested, f"{path}[{index}]")


def _read_jsonl(path: Path, name: str) -> list[Mapping[str, object]]:
    rows: list[Mapping[str, object]] = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, Mapping):
            raise ValueError(f"{name} line {line_number} must be an object")
        rows.append(value)
    if not rows:
        raise ValueError(f"{name} must not be empty")
    return rows


def _closed(value: object, fields: set[str], name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError(f"{name} must be a closed mapping")
    return value


def _decode_visible(row: Mapping[str, object]) -> dict[str, object]:
    _reject_sealed_evidence(row)
    _closed(row, _VISIBLE_FIELDS, "visible telemetry")
    if row["schema_version"] != VISIBLE_SCHEMA:
        raise ValueError("unsupported visible telemetry schema")
    case_id = _required_text(row["case_id"], "case_id")
    event_index = row["observed_at_event_index"]
    if isinstance(event_index, bool) or not isinstance(event_index, int) or event_index < 0:
        raise ValueError("observed_at_event_index must be non-negative")
    state_root = _required_text(row["state_root"], "state_root")
    source_root = _required_text(row["source_manifest_root"], "source_manifest_root")

    pipeline = _closed(row["pipeline_checks"], _PIPELINE_FIELDS, "pipeline_checks")
    if any(not isinstance(value, bool) for value in pipeline.values()):
        raise ValueError("pipeline checks must be booleans")
    failed_pipeline = [name for name in sorted(_PIPELINE_FIELDS) if not pipeline[name]]

    versions = row["active_versions"]
    if not isinstance(versions, list):
        raise ValueError("active_versions must be a list")
    by_slot: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for value in versions:
        item = _closed(value, _VERSION_FIELDS, "active version")
        slot = _required_text(item["slot"], "active version slot")
        memory_id = _required_text(item["memory_id"], "active version memory_id")
        observed_at = item["observed_at"]
        if isinstance(observed_at, bool) or not isinstance(observed_at, int) or observed_at < 0:
            raise ValueError("active version observed_at must be non-negative")
        by_slot[slot].append((observed_at, memory_id))
    conflicts = [items for items in by_slot.values() if len(items) > 1]
    usable_conflict: list[tuple[int, str]] | None = None
    if len(conflicts) == 1 and len({item[0] for item in conflicts[0]}) == len(conflicts[0]):
        usable_conflict = sorted(conflicts[0])

    integrity = row["integrity_signals"]
    if not isinstance(integrity, list):
        raise ValueError("integrity_signals must be a list")
    poison_suspects: list[str] = []
    cas_anomaly = False
    influence_anomaly = False
    for value in integrity:
        item = _closed(value, _INTEGRITY_FIELDS, "integrity signal")
        memory_id = _required_text(item["memory_id"], "integrity memory_id")
        cas_valid = item["cas_valid"]
        score = item["influence_score"]
        threshold = item["influence_threshold"]
        if not isinstance(cas_valid, bool):
            raise ValueError("cas_valid must be boolean")
        if (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
        ):
            raise ValueError("influence score and threshold must be numeric")
        item_cas = not cas_valid
        item_influence = float(score) > float(threshold)
        if item_cas or item_influence:
            poison_suspects.append(memory_id)
        cas_anomaly = cas_anomaly or item_cas
        influence_anomaly = influence_anomaly or item_influence

    active: list[str] = []
    if len(failed_pipeline) == 1:
        active.append("process_fault")
    if usable_conflict is not None:
        active.append("state_drift")
    if poison_suspects:
        active.append("adversarial_poison")
    structurally_ambiguous = len(failed_pipeline) > 1 or len(conflicts) > 1 or (
        len(conflicts) == 1 and usable_conflict is None
    )
    if not active and not structurally_ambiguous:
        return {
            "case_id": case_id,
            "decision": "abstain",
            "repair_admitted": False,
            "abstain_reason": "no_fault",
            "syndrome": None,
        }
    if len(active) != 1 or structurally_ambiguous:
        return {
            "case_id": case_id,
            "decision": "abstain",
            "repair_admitted": False,
            "abstain_reason": "ambiguous_mechanisms",
            "syndrome": None,
        }

    mechanism = active[0]
    ordered_ids: list[str] = []
    superseding: str | None = None
    superseded: str | None = None
    process_subtype: str | None = None
    if mechanism == "process_fault":
        process_subtype = failed_pipeline[0]
        signal_ids = [f"pipeline-check-failed:{process_subtype}"]
    elif mechanism == "state_drift":
        assert usable_conflict is not None
        ordered_ids = [memory_id for _, memory_id in usable_conflict]
        superseding = ordered_ids[-1]
        superseded = ordered_ids[-2]
        signal_ids = ["active-slot-version-conflict", "observed-write-order"]
    else:
        signal_ids = []
        if cas_anomaly:
            signal_ids.append("cas-mismatch")
        if influence_anomaly:
            signal_ids.append("influence-threshold-exceeded")
    observation = {
        "observation_id": f"p4c3-observation-{case_id}",
        "incident_id": f"p4c3-incident-{case_id}",
        "observed_at_event_index": event_index,
        "state_root": state_root,
        "source_manifest_root": source_root,
        "process_fault_subtype": process_subtype,
        "observed_order": ordered_ids,
        "superseding_memory_id": superseding,
        "superseded_memory_id": superseded,
        "cas_anomaly": cas_anomaly if mechanism == "adversarial_poison" else False,
        "influence_anomaly": influence_anomaly if mechanism == "adversarial_poison" else False,
        "suspect_ids": sorted(set(poison_suspects)) if mechanism == "adversarial_poison" else [],
        "signal_ids": signal_ids,
        "provenance": {
            "detector": "p4c3-native-visible-telemetry-v1",
            "visible_telemetry_sha256": content_sha256(row, ensure_ascii=False, allow_nan=False),
        },
    }
    syndrome = MemAuditEccAdapter().decode(observation)
    return {
        "case_id": case_id,
        "decision": "syndrome",
        "repair_admitted": True,
        "abstain_reason": None,
        "syndrome": syndrome.to_mapping(),
    }


def run_p4c3_detection(*, visible_telemetry: Path, output_dir: Path) -> dict[str, object]:
    """Run native detection using deployment-visible telemetry only."""
    visible_telemetry = Path(visible_telemetry)
    output_dir = Path(output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("fresh P4C-3 runtime refuses a non-empty output directory")
    source_rows = _read_jsonl(visible_telemetry, "visible telemetry")
    detections = [_decode_visible(row) for row in source_rows]
    case_ids = [str(row["case_id"]) for row in detections]
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("visible telemetry case_id values must be unique")
    output_dir.mkdir(parents=True, exist_ok=True)
    detection_path = output_dir / "detections.jsonl"
    for detection in detections:
        append_jsonl_fsync(detection_path, detection, ensure_ascii=False, allow_nan=False)
    mechanism_counts = Counter(
        row["syndrome"]["mechanism"]
        for row in detections
        if isinstance(row["syndrome"], Mapping)
    )
    manifest: dict[str, object] = {
        "schema_version": RUNTIME_SCHEMA,
        "status": "prediction_sealed",
        "runtime_gold_free": True,
        "sealed_sidecar_opened": False,
        "external_call_count": 0,
        "same_trace_answer_replay": False,
        "paper_role": "mainline",
        "primary_claim": "gold-free memory fault correction and evolution",
        "visible_telemetry_sha256": _sha256(visible_telemetry),
        "detection_sha256": _sha256(detection_path),
        "case_count": len(detections),
        "syndrome_count": sum(mechanism_counts.values()),
        "repair_admission_count": sum(row["repair_admitted"] is True for row in detections),
        "abstain_count": sum(row["decision"] == "abstain" for row in detections),
        "mechanism_counts": {name: mechanism_counts[name] for name in MECHANISMS},
        "claim_scope": "native_detection_over_deployment_visible_telemetry_not_repair_efficacy",
    }
    manifest_path = output_dir / "runtime_manifest.json"
    atomic_json_write(manifest_path, manifest, ensure_ascii=False, allow_nan=False, indent=2, trailing_newline=True)
    result = dict(manifest)
    result["manifest_sha256"] = _sha256(manifest_path)
    return result


def audit_p4c3_detection(*, output_dir: Path, sealed_sidecar: Path) -> dict[str, object]:
    """Open sealed labels only after a complete runtime manifest exists."""
    output_dir = Path(output_dir)
    sealed_sidecar = Path(sealed_sidecar)
    manifest_path = output_dir / "runtime_manifest.json"
    detection_path = output_dir / "detections.jsonl"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "prediction_sealed" or manifest.get("sealed_sidecar_opened") is not False:
        raise ValueError("P4C-3 audit requires a sealed gold-free runtime")
    if manifest.get("detection_sha256") != _sha256(detection_path):
        raise ValueError("P4C-3 detection root changed after seal")
    detections = _read_jsonl(detection_path, "P4C-3 detections")
    sidecar_rows = _read_jsonl(sealed_sidecar, "sealed sidecar")
    labels: dict[str, str] = {}
    for row in sidecar_rows:
        if set(row) != {"case_id", "label"}:
            raise ValueError("sealed sidecar rows must contain only case_id and label")
        case_id = _required_text(row["case_id"], "sidecar case_id")
        label = str(row["label"])
        if label not in LABELS or case_id in labels:
            raise ValueError("sealed sidecar labels must be unique and closed")
        labels[case_id] = label
    detected_by_case = {str(row["case_id"]): row for row in detections}
    if set(labels) != set(detected_by_case):
        raise ValueError("sealed sidecar must exactly cover runtime cases")

    confusion: dict[str, Counter[str]] = {label: Counter() for label in LABELS}
    normalized_correct = 0
    false_repairs = 0
    no_fault_count = 0
    predicted: dict[str, str] = {}
    for case_id, truth in labels.items():
        detection = detected_by_case[case_id]
        raw_prediction = (
            str(detection["syndrome"]["mechanism"])
            if isinstance(detection.get("syndrome"), Mapping)
            else "abstain"
        )
        confusion[truth][raw_prediction] += 1
        prediction = "no_fault" if raw_prediction == "abstain" and detection.get("abstain_reason") == "no_fault" else raw_prediction
        predicted[case_id] = prediction
        normalized_correct += prediction == truth
        if truth == "no_fault":
            no_fault_count += 1
            false_repairs += prediction in MECHANISMS

    per_class: dict[str, object] = {}
    for label in LABELS:
        true_positive = sum(labels[case_id] == label and predicted[case_id] == label for case_id in labels)
        predicted_positive = sum(value == label for value in predicted.values())
        actual_positive = sum(value == label for value in labels.values())
        per_class[label] = {
            "support": actual_positive,
            "precision": true_positive / predicted_positive if predicted_positive else 0.0,
            "recall": true_positive / actual_positive if actual_positive else 0.0,
        }
    report: dict[str, object] = {
        "schema_version": AUDIT_SCHEMA,
        "runtime_manifest_sha256": _sha256(manifest_path),
        "detection_sha256": _sha256(detection_path),
        "sidecar_sha256": _sha256(sealed_sidecar),
        "case_count": len(labels),
        "accuracy": normalized_correct / len(labels),
        "false_repair_rate": false_repairs / no_fault_count if no_fault_count else 0.0,
        "per_class": per_class,
        "confusion": {truth: dict(confusion[truth]) for truth in LABELS},
        "runtime_feedback_written": False,
        "paper_role": "mainline",
        "external_call_count": 0,
        "claim_scope": "sealed_detector_precision_recall_confusion_and_false_repair_only",
    }
    atomic_json_write(output_dir / "detector_audit.json", report, ensure_ascii=False, allow_nan=False, indent=2, trailing_newline=True)
    return report


def prepare_detection_sidecar(
    *, output_dir: Path, incident_overlay: Path, sealed_sidecar: Path
) -> dict[str, object]:
    """Project public injection mechanisms only after runtime is sealed."""
    output_dir = Path(output_dir)
    incident_overlay = Path(incident_overlay)
    sealed_sidecar = Path(sealed_sidecar)
    if sealed_sidecar.exists():
        raise ValueError("P4C-3 sidecar preparation refuses to overwrite output")
    manifest_path = output_dir / "runtime_manifest.json"
    detection_path = output_dir / "detections.jsonl"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("status") != "prediction_sealed"
        or manifest.get("sealed_sidecar_opened") is not False
        or manifest.get("detection_sha256") != _sha256(detection_path)
    ):
        raise ValueError("P4C-3 sidecar preparation requires an intact sealed runtime")
    detection_cases = {
        str(row["case_id"]) for row in _read_jsonl(detection_path, "P4C-3 detections")
    }
    labels: dict[str, str] = {}
    for row in _read_jsonl(incident_overlay, "incident overlay"):
        schema = row.get("schema_version")
        case_id = _required_text(row.get("case_id"), "overlay case_id")
        if schema == "cmd-p4c1-incident-overlay-v1":
            mechanism = str(row.get("mechanism"))
        elif schema == "cmd-p4c3-audit-overlay-v1" and set(row) == {
            "schema_version", "case_id", "label"
        }:
            mechanism = str(row.get("label"))
        else:
            raise ValueError("P4C-3 sidecar requires a closed audit overlay")
        if mechanism not in LABELS or case_id in labels:
            raise ValueError("P4C-3 incident overlay mechanisms must be unique and closed")
        labels[case_id] = mechanism
    if set(labels) != detection_cases:
        raise ValueError("incident overlay must exactly cover the sealed detector cases")
    sealed_sidecar.parent.mkdir(parents=True, exist_ok=True)
    for case_id in sorted(labels):
        append_jsonl_fsync(
            sealed_sidecar,
            {"case_id": case_id, "label": labels[case_id]},
            ensure_ascii=False,
            allow_nan=False,
        )
    return {
        "schema_version": "cmd-p4c3-sidecar-preparation-v1",
        "status": "sealed_sidecar_ready",
        "runtime_manifest_sha256": _sha256(manifest_path),
        "incident_overlay_sha256": _sha256(incident_overlay),
        "sidecar_sha256": _sha256(sealed_sidecar),
        "case_count": len(labels),
        "runtime_feedback_written": False,
        "paper_role": "mainline-supporting-sidecar",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", choices=("runtime", "prepare-sidecar", "audit"), default="runtime"
    )
    parser.add_argument("--visible-telemetry", type=Path)
    parser.add_argument("--incident-overlay", type=Path)
    parser.add_argument("--sealed-sidecar", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.mode == "runtime":
        if (
            args.visible_telemetry is None
            or args.sealed_sidecar is not None
            or args.incident_overlay is not None
        ):
            parser.error(
                "runtime requires --visible-telemetry and rejects sealed evidence"
            )
        result = run_p4c3_detection(visible_telemetry=args.visible_telemetry, output_dir=args.output_dir)
    elif args.mode == "prepare-sidecar":
        if (
            args.incident_overlay is None
            or args.sealed_sidecar is None
            or args.visible_telemetry is not None
        ):
            parser.error(
                "prepare-sidecar requires --incident-overlay and --sealed-sidecar"
            )
        result = prepare_detection_sidecar(
            output_dir=args.output_dir,
            incident_overlay=args.incident_overlay,
            sealed_sidecar=args.sealed_sidecar,
        )
    else:
        if (
            args.sealed_sidecar is None
            or args.visible_telemetry is not None
            or args.incident_overlay is not None
        ):
            parser.error("audit requires --sealed-sidecar and does not reopen visible telemetry")
        result = audit_p4c3_detection(output_dir=args.output_dir, sealed_sidecar=args.sealed_sidecar)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


__all__ = [
    "audit_p4c3_detection",
    "prepare_detection_sidecar",
    "run_p4c3_detection",
]


if __name__ == "__main__":
    raise SystemExit(main())
