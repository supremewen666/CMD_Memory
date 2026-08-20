#!/usr/bin/env python3
"""E1 sealed-protocol registration, preflight verification and outer audit."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Mapping, Sequence

from cmd_audit.eval.anchor_discipline import Anchor, AnchorSet, SealedProtocol


E1_REGISTRATION_SCHEMA_VERSION = "cmd-e1-sealed-registration-v1"
E1_PREFLIGHT_SCHEMA_VERSION = "cmd-e1-sealed-preflight-v1"
E1_AUDIT_SCHEMA_VERSION = "cmd-e1-held-out-audit-v1"


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a JSON object")
    return value


def _load_anchor_set(path: Path, *, set_id: str) -> AnchorSet:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        raw = _mapping(json.loads(line), f"anchor row {number}")
        if set(raw) != {"anchor_id", "payload", "reference", "split"}:
            raise ValueError("anchor row is not closed")
        if raw["split"] not in {"reference", "held_out"}:
            raise ValueError("anchor split must be reference or held_out")
        rows.append((raw["split"], Anchor(raw["anchor_id"], raw["payload"], raw["reference"])))
    reference = tuple(row for split, row in rows if split == "reference")
    held_out = tuple(row for split, row in rows if split == "held_out")
    if len(reference) != 10:
        raise ValueError("E1 requires exactly ten readable reference anchors")
    return AnchorSet(reference=reference, held_out=held_out, set_id=set_id)


def _atomic_json(path: Path, value: object) -> None:
    if path.exists():
        raise ValueError(f"refusing to overwrite E1 artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def seal_protocol(
    *,
    dataset: Path,
    anchors: Path,
    output: Path,
    protocol_id: str,
    anchor_set_id: str,
    arms: Sequence[str],
    primary_metric: str,
    thresholds: Mapping[str, float],
    seeds: Sequence[int],
) -> dict[str, object]:
    anchor_set = _load_anchor_set(anchors, set_id=anchor_set_id)
    protocol = SealedProtocol(
        protocol_id=protocol_id,
        dataset_sha256=_file_sha256(dataset),
        arms=tuple(arms),
        primary_metric=primary_metric,
        thresholds=thresholds,
        seeds=tuple(seeds),
        anchor_fingerprint=anchor_set.fingerprint(),
    )
    payload = {
        "schema_version": E1_REGISTRATION_SCHEMA_VERSION,
        "protocol": protocol.to_mapping(),
        "protocol_sha256": protocol.protocol_sha256,
        "dataset_path": str(dataset.resolve()),
        "anchors_path": str(anchors.resolve()),
        "anchors_file_sha256": _file_sha256(anchors),
        "anchor_set_id": anchor_set_id,
        "reference_anchor_count": anchor_set.reference_size,
        "held_out_anchor_count": anchor_set.held_out_size,
        "held_out_content_exposed": False,
    }
    _atomic_json(output, payload)
    return payload


def _restore_protocol(registration: Mapping[str, object]) -> SealedProtocol:
    if registration.get("schema_version") != E1_REGISTRATION_SCHEMA_VERSION:
        raise ValueError("E1 registration schema mismatch")
    raw = _mapping(registration.get("protocol"), "sealed protocol")
    if raw.get("schema_version") != "cmd-sealed-protocol-v1":
        raise ValueError("sealed protocol schema mismatch")
    protocol = SealedProtocol(
        protocol_id=raw["protocol_id"],
        dataset_sha256=raw["dataset_sha256"],
        arms=tuple(raw["arms"]),
        primary_metric=raw["primary_metric"],
        thresholds=raw["thresholds"],
        seeds=tuple(raw["seeds"]),
        anchor_fingerprint=raw["anchor_fingerprint"],
    )
    if registration.get("protocol_sha256") != protocol.protocol_sha256:
        raise ValueError("sealed protocol hash mismatch")
    return protocol


def verify_registration(
    *, registration_path: Path, dataset: Path, anchors: Path, output: Path
) -> dict[str, object]:
    registration = _mapping(json.loads(registration_path.read_text(encoding="utf-8")), "registration")
    protocol = _restore_protocol(registration)
    if registration.get("anchors_file_sha256") != _file_sha256(anchors):
        raise ValueError("anchor file changed after registration")
    anchor_set = _load_anchor_set(anchors, set_id=registration["anchor_set_id"])
    protocol.verify_run(
        dataset_sha256=_file_sha256(dataset),
        arms=protocol.arms,
        primary_metric=protocol.primary_metric,
        seeds=protocol.seeds,
        anchor_set=anchor_set,
    )
    payload = {
        "schema_version": E1_PREFLIGHT_SCHEMA_VERSION,
        "registration_sha256": _file_sha256(registration_path),
        "protocol_sha256": protocol.protocol_sha256,
        "dataset_sha256": _file_sha256(dataset),
        "anchors_file_sha256": _file_sha256(anchors),
        "verified": True,
        "held_out_read": False,
        "model_calls": 0,
        "network_calls": 0,
    }
    _atomic_json(output, payload)
    return payload


def audit_held_out(
    *,
    registration_path: Path,
    dataset: Path,
    anchors: Path,
    scores: Path,
    output: Path,
) -> dict[str, object]:
    registration = _mapping(json.loads(registration_path.read_text(encoding="utf-8")), "registration")
    protocol = _restore_protocol(registration)
    if registration.get("anchors_file_sha256") != _file_sha256(anchors):
        raise ValueError("anchor file changed after registration")
    anchor_set = _load_anchor_set(anchors, set_id=registration["anchor_set_id"])
    protocol.verify_run(
        dataset_sha256=_file_sha256(dataset),
        arms=protocol.arms,
        primary_metric=protocol.primary_metric,
        seeds=protocol.seeds,
        anchor_set=anchor_set,
    )
    observed: dict[str, float] = {}
    for number, line in enumerate(scores.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        raw = _mapping(json.loads(line), f"held-out score row {number}")
        if set(raw) != {"anchor_id", "observed"}:
            raise ValueError("held-out score row is not closed")
        anchor_id = raw["anchor_id"]
        if not isinstance(anchor_id, str) or not anchor_id or anchor_id in observed:
            raise ValueError("held-out score IDs must be unique")
        value = raw["observed"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("held-out observed score must be numeric")
        observed[anchor_id] = float(value)
    audit = anchor_set.audit_scores(observed)
    threshold = protocol.thresholds.get("max_anchor_mean_absolute_deviation")
    passed = threshold is not None and audit["mean_absolute_deviation"] <= threshold
    result = {
        "schema_version": E1_AUDIT_SCHEMA_VERSION,
        "registration_sha256": _file_sha256(registration_path),
        "protocol_sha256": protocol.protocol_sha256,
        "scores_sha256": _file_sha256(scores),
        "audit": audit,
        "threshold": threshold,
        "passed": passed,
        "model_calls_new": 0,
        "network_calls_new": 0,
    }
    _atomic_json(output, result)
    return result


def _thresholds(value: str) -> Mapping[str, float]:
    raw = _mapping(json.loads(value), "thresholds")
    return {str(key): float(item) for key, item in raw.items()}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    seal = sub.add_parser("seal")
    seal.add_argument("--dataset", type=Path, required=True)
    seal.add_argument("--anchors", type=Path, required=True)
    seal.add_argument("--output", type=Path, required=True)
    seal.add_argument("--protocol-id", required=True)
    seal.add_argument("--anchor-set-id", required=True)
    seal.add_argument("--arm", action="append", required=True)
    seal.add_argument("--primary-metric", required=True)
    seal.add_argument("--thresholds-json", required=True)
    seal.add_argument("--seed", type=int, action="append", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--registration", type=Path, required=True)
    verify.add_argument("--dataset", type=Path, required=True)
    verify.add_argument("--anchors", type=Path, required=True)
    verify.add_argument("--output", type=Path, required=True)
    audit = sub.add_parser("audit")
    audit.add_argument("--registration", type=Path, required=True)
    audit.add_argument("--dataset", type=Path, required=True)
    audit.add_argument("--anchors", type=Path, required=True)
    audit.add_argument("--scores", type=Path, required=True)
    audit.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.action == "seal":
        result = seal_protocol(
            dataset=args.dataset,
            anchors=args.anchors,
            output=args.output,
            protocol_id=args.protocol_id,
            anchor_set_id=args.anchor_set_id,
            arms=args.arm,
            primary_metric=args.primary_metric,
            thresholds=_thresholds(args.thresholds_json),
            seeds=args.seed,
        )
    elif args.action == "verify":
        result = verify_registration(
            registration_path=args.registration,
            dataset=args.dataset,
            anchors=args.anchors,
            output=args.output,
        )
    else:
        result = audit_held_out(
            registration_path=args.registration,
            dataset=args.dataset,
            anchors=args.anchors,
            scores=args.scores,
            output=args.output,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
