#!/usr/bin/env python3
"""Freeze and validate prospective GHOST data, access, and delayed feedback.

This module is deliberately stdlib-only and makes no model or network calls.  It
does not manufacture sealed data: ``freeze`` requires a curator-provided case
stream, four explicit case-id lists, an independent-source attestation, and a
model manifest.  The resulting protocol is immutable and content addressed.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
from statistics import fmean
from typing import Mapping, Sequence


PROTOCOL_SCHEMA = "cmd-ghost-prospective-freeze-v2"
ATTESTATION_SCHEMA = "cmd-ghost-independent-source-attestation-v1"
MODEL_MANIFEST_SCHEMA = "cmd-ghost-model-manifest-v1"
AUTHORIZATION_SCHEMA = "cmd-ghost-first-test-authorization-v1"
ACCESS_EVENT_SCHEMA = "cmd-ghost-access-event-v1"
FEEDBACK_SCHEMA = "cmd-ghost-delayed-deployment-feedback-v1"
FEEDBACK_REPORT_SCHEMA = "cmd-ghost-delayed-feedback-audit-v1"
PARTITIONS = ("ghost_dev", "ghost_cal", "ghost_test_rep", "ghost_test_new")
MODEL_ROLES = frozenset(
    {"relation_instrument", "intent_proposer", "answer", "judge"}
)
SIGNALS = (
    "target_resolved",
    "anchor_non_regression",
    "recurrence",
    "annotation_consumed",
)
EFFECT_SIGNALS = {
    "verify": ("target_resolved", "anchor_non_regression", "recurrence"),
    "abstain": ("anchor_non_regression", "recurrence"),
    "annotate_conflict": (
        "anchor_non_regression", "recurrence", "annotation_consumed"
    ),
    "demote": ("target_resolved", "anchor_non_regression", "recurrence"),
    "suppress": ("target_resolved", "anchor_non_regression", "recurrence"),
    "replace": ("target_resolved", "anchor_non_regression", "recurrence"),
}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a JSON object")
    return value


def _load_json(path: Path, name: str) -> Mapping[str, object]:
    return _mapping(json.loads(path.read_text(encoding="utf-8")), name)


def _load_jsonl(path: Path) -> tuple[Mapping[str, object], ...]:
    rows: list[Mapping[str, object]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(_mapping(json.loads(line), f"{path}:{line_number}"))
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSONL at {path}:{line_number}") from error
    if not rows:
        raise ValueError(f"case stream is empty: {path}")
    return tuple(rows)


def _atomic_json(path: Path, value: object) -> None:
    if path.exists():
        raise ValueError(f"refusing to overwrite immutable artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _append_jsonl(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(_canonical(value).decode("utf-8") + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _require_hash(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return value


def _ids(path: Path) -> tuple[str, ...]:
    result = tuple(
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    if not result or len(set(result)) != len(result):
        raise ValueError(f"partition list must be non-empty and unique: {path}")
    return result


def _case_rows(cases: Path, candidate_budget: int) -> dict[str, Mapping[str, object]]:
    rows = _load_jsonl(cases)
    result: dict[str, Mapping[str, object]] = {}
    for row in rows:
        case_id = row.get("case_id")
        family_id = row.get("family_id")
        intents = row.get("intents")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("every prospective case requires case_id")
        if not isinstance(family_id, str) or not family_id:
            raise ValueError("every prospective case requires family_id")
        if not isinstance(intents, list) or len(intents) != candidate_budget:
            raise ValueError("every prospective case must match candidate_budget")
        if case_id in result:
            raise ValueError(f"prospective case_id is repeated: {case_id}")
        result[case_id] = row
    return result


def _validate_attestation(
    value: Mapping[str, object], *, cases: Path, partition_files: Mapping[str, Path]
) -> None:
    expected = {
        "schema_version",
        "independent_source",
        "source_id",
        "collector",
        "collected_at_utc",
        "cases_file_sha256",
        "partition_file_sha256",
        "notes",
    }
    if set(value) != expected or value.get("schema_version") != ATTESTATION_SCHEMA:
        raise ValueError("independent-source attestation is not closed or versioned")
    if value.get("independent_source") is not True:
        raise ValueError("sealed protocol requires independent_source=true")
    for key in ("source_id", "collector", "collected_at_utc"):
        if not isinstance(value.get(key), str) or not value[key]:
            raise ValueError(f"attestation requires {key}")
    if value.get("cases_file_sha256") != _file_sha256(cases):
        raise ValueError("attested case stream hash mismatch")
    hashes = _mapping(value.get("partition_file_sha256"), "partition hashes")
    if set(hashes) != set(PARTITIONS):
        raise ValueError("attestation must bind all four partition files")
    for partition, path in partition_files.items():
        if hashes.get(partition) != _file_sha256(path):
            raise ValueError(f"attested partition hash mismatch: {partition}")


def _validate_model_manifest(value: Mapping[str, object]) -> None:
    if set(value) != {"schema_version", "models"}:
        raise ValueError("model manifest is not closed")
    if value.get("schema_version") != MODEL_MANIFEST_SCHEMA:
        raise ValueError("model manifest schema mismatch")
    models = value.get("models")
    if not isinstance(models, list) or not models:
        raise ValueError("model manifest must contain at least one model")
    roles: set[str] = set()
    for raw in models:
        row = _mapping(raw, "model identity")
        if set(row) != {"role", "model_id", "model_sha256"}:
            raise ValueError("model identity is not closed")
        role = row.get("role")
        model_id = row.get("model_id")
        if not isinstance(role, str) or not role or role in roles:
            raise ValueError("model roles must be non-empty and unique")
        if not isinstance(model_id, str) or not model_id:
            raise ValueError("model_id must be non-empty")
        _require_hash(row.get("model_sha256"), f"model hash:{role}")
        roles.add(role)
    if roles != MODEL_ROLES:
        raise ValueError(
            "model manifest must freeze relation_instrument, intent_proposer, "
            "answer, and judge"
        )


def _code_manifest(root: Path) -> dict[str, object]:
    relative = (
        "cmd_audit/repair/deployment_feedback_evaluator.py",
        "cmd_audit/repair/ghost_ecology.py",
        "experiments/ghost_live_protocol.py",
        "experiments/v4_live_materialization.py",
        "experiments/v4_materialization.py",
        "experiments/v4_prequential_runner.py",
        "run_remaining_experiments.sh",
    )
    files = {}
    for name in relative:
        path = root / name
        if not path.is_file():
            raise ValueError(f"registered live code file is missing: {path}")
        files[name] = _file_sha256(path)
    return {
        "root_at_freeze": str(root.resolve()),
        "files": files,
        "tree_sha256": _sha256(files),
    }


def _access_event(
    *, event: str, protocol_sha256: str, previous_sha256: str | None, reason: str
) -> dict[str, object]:
    body = {
        "schema_version": ACCESS_EVENT_SCHEMA,
        "timestamp": _now(),
        "event": event,
        "protocol_sha256": protocol_sha256,
        "previous_event_sha256": previous_sha256,
        "reason": reason,
    }
    return {**body, "event_sha256": _sha256(body)}


def freeze(
    *,
    root: Path,
    cases: Path,
    partition_files: Mapping[str, Path],
    attestation: Path,
    model_manifest: Path,
    evaluator: Path,
    preparation_manifest: Path,
    output: Path,
    access_ledger: Path,
    candidate_budget: int,
) -> Mapping[str, object]:
    if candidate_budget < 1:
        raise ValueError("candidate_budget must be positive")
    rows = _case_rows(cases, candidate_budget)
    assignments: dict[str, str] = {}
    split_ids: dict[str, tuple[str, ...]] = {}
    for partition in PARTITIONS:
        ids = _ids(partition_files[partition])
        split_ids[partition] = ids
        for case_id in ids:
            if case_id not in rows:
                raise ValueError(f"partition references unknown case: {case_id}")
            if case_id in assignments:
                raise ValueError(f"case appears in multiple partitions: {case_id}")
            assignments[case_id] = partition
    if set(assignments) != set(rows):
        missing = sorted(set(rows) - set(assignments))
        raise ValueError(f"partition lists do not exactly cover cases: missing={missing}")
    families = {
        partition: {str(rows[case_id]["family_id"]) for case_id in ids}
        for partition, ids in split_ids.items()
    }
    if families["ghost_dev"] & families["ghost_cal"]:
        raise ValueError("ghost_dev and ghost_cal must be family-disjoint")
    if not families["ghost_test_rep"] <= families["ghost_dev"]:
        raise ValueError("ghost_test_rep must use represented ghost_dev families")
    if families["ghost_test_new"] & (
        families["ghost_dev"] | families["ghost_cal"] | families["ghost_test_rep"]
    ):
        raise ValueError("ghost_test_new families must be unseen")
    attested = _load_json(attestation, "independent-source attestation")
    _validate_attestation(attested, cases=cases, partition_files=partition_files)
    models = _load_json(model_manifest, "model manifest")
    _validate_model_manifest(models)
    evaluator_hash = _file_sha256(evaluator)
    preparation_manifest_hash = _file_sha256(preparation_manifest)
    code = _code_manifest(root)
    body: dict[str, object] = {
        "schema_version": PROTOCOL_SCHEMA,
        "created_at_utc": _now(),
        "confirmatory_status": "FROZEN_AWAITING_FIRST_TEST_AUTHORIZATION",
        "cases": str(cases.resolve()),
        "cases_file_sha256": _file_sha256(cases),
        "candidate_budget": candidate_budget,
        "partition_policy": {
            "case_disjoint": True,
            "dev_cal_family_disjoint": True,
            "test_rep_reuses_dev_families_only": True,
            "test_new_family_disjoint": True,
            "test_updates_authorized": False,
        },
        "partition_counts": {
            partition: len(split_ids[partition]) for partition in PARTITIONS
        },
        "assignments": [
            {
                "case_id": case_id,
                "family_id": str(rows[case_id]["family_id"]),
                "partition": assignments[case_id],
            }
            for case_id in sorted(assignments)
        ],
        "independent_source_attestation": dict(attested),
        "independent_source_attestation_file_sha256": _file_sha256(attestation),
        "model_manifest": dict(models),
        "model_manifest_file_sha256": _file_sha256(model_manifest),
        "evaluator": str(evaluator.resolve()),
        "evaluator_file_sha256": evaluator_hash,
        "preparation_manifest": str(preparation_manifest.resolve()),
        "preparation_manifest_file_sha256": preparation_manifest_hash,
        "code": code,
        "first_test_access_authorized": False,
        "feedback_contract": {
            "schema_version": FEEDBACK_SCHEMA,
            "development_proxy_allowed": False,
            "gold_derived_allowed": False,
            "right_censored_is_not_failure": True,
            "required_signals": [
                "target_resolved",
                "anchor_non_regression",
                "recurrence",
                "annotation_consumed",
            ],
        },
    }
    protocol = {**body, "protocol_sha256": _sha256(body)}
    _atomic_json(output, protocol)
    event = _access_event(
        event="protocol_frozen_test_access_denied",
        protocol_sha256=str(protocol["protocol_sha256"]),
        previous_sha256=None,
        reason="sealed test is denied until explicit authorization",
    )
    if access_ledger.exists():
        raise ValueError(f"refusing to reuse access ledger: {access_ledger}")
    _append_jsonl(access_ledger, event)
    return protocol


def _validated_protocol(path: Path, *, root: Path | None = None) -> Mapping[str, object]:
    value = _load_json(path, "GHOST protocol")
    if value.get("schema_version") != PROTOCOL_SCHEMA:
        raise ValueError("GHOST prospective protocol schema mismatch")
    body = dict(value)
    claimed = body.pop("protocol_sha256", None)
    if _sha256(body) != claimed:
        raise ValueError("GHOST prospective protocol hash mismatch")
    counts = _mapping(value.get("partition_counts"), "partition counts")
    if set(counts) != set(PARTITIONS) or any(
        isinstance(counts[name], bool)
        or not isinstance(counts[name], int)
        or counts[name] <= 0
        for name in PARTITIONS
    ):
        raise ValueError("all four prospective partitions must be non-empty")
    _validate_model_manifest(_mapping(value.get("model_manifest"), "model manifest"))
    if root is not None:
        current_code = _code_manifest(root)
        frozen_code = _mapping(value.get("code"), "frozen code manifest")
        if (
            current_code["files"] != frozen_code.get("files")
            or current_code["tree_sha256"] != frozen_code.get("tree_sha256")
        ):
            raise ValueError("current live code differs from frozen protocol")
    return value


def authorize(
    *,
    protocol_path: Path,
    access_ledger: Path,
    output: Path,
    authorizer: str,
    run_id: str,
) -> Mapping[str, object]:
    protocol = _validated_protocol(protocol_path)
    if not authorizer or not run_id or any(
        character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
        for character in run_id
    ):
        raise ValueError("authorizer and safe run_id must be non-empty")
    if not access_ledger.exists():
        raise ValueError("access ledger is missing")
    events = _load_jsonl(access_ledger)
    previous = events[-1].get("event_sha256")
    _require_hash(previous, "previous access event hash")
    body = {
        "schema_version": AUTHORIZATION_SCHEMA,
        "authorized_at_utc": _now(),
        "authorizer": authorizer,
        "run_id": run_id,
        "protocol_sha256": protocol["protocol_sha256"],
        "allowed_partitions": ["ghost_test_rep", "ghost_test_new"],
        "updates_authorized": False,
        "single_confirmatory_access": True,
        "previous_access_event_sha256": previous,
    }
    result = {**body, "authorization_sha256": _sha256(body)}
    _atomic_json(output, result)
    _append_jsonl(
        access_ledger,
        _access_event(
            event="first_test_access_authorized",
            protocol_sha256=str(protocol["protocol_sha256"]),
            previous_sha256=str(previous),
            reason=f"authorized by {authorizer}",
        ),
    )
    return result


def validate_run(
    *,
    root: Path,
    cases: Path,
    protocol_path: Path,
    authorization_path: Path,
    access_ledger_path: Path,
    model_manifest_path: Path,
    evaluator_path: Path,
    preparation_manifest_path: Path,
    candidate_budget: int,
    run_id: str,
) -> Mapping[str, object]:
    protocol = _validated_protocol(protocol_path, root=root)
    if protocol.get("cases_file_sha256") != _file_sha256(cases):
        raise ValueError("run case stream differs from frozen protocol")
    if protocol.get("candidate_budget") != candidate_budget:
        raise ValueError("run candidate budget differs from frozen protocol")
    _case_rows(cases, candidate_budget)
    if protocol.get("model_manifest_file_sha256") != _file_sha256(model_manifest_path):
        raise ValueError("run model manifest differs from frozen protocol")
    if protocol.get("evaluator_file_sha256") != _file_sha256(evaluator_path):
        raise ValueError("run evaluator differs from frozen protocol")
    if protocol.get("preparation_manifest_file_sha256") != _file_sha256(
        preparation_manifest_path
    ):
        raise ValueError("run preparation manifest differs from frozen protocol")
    authorization = _load_json(authorization_path, "test authorization")
    body = dict(authorization)
    claimed = body.pop("authorization_sha256", None)
    if (
        authorization.get("schema_version") != AUTHORIZATION_SCHEMA
        or _sha256(body) != claimed
        or authorization.get("protocol_sha256") != protocol.get("protocol_sha256")
        or authorization.get("updates_authorized") is not False
        or authorization.get("run_id") != run_id
    ):
        raise ValueError("test authorization is invalid or belongs to another protocol")
    events = _load_jsonl(access_ledger_path)
    if len(events) != 2:
        raise ValueError("access ledger must contain exactly freeze and authorization events")
    previous: str | None = None
    for index, raw in enumerate(events):
        row = dict(raw)
        claimed_event_hash = row.pop("event_sha256", None)
        if (
            row.get("schema_version") != ACCESS_EVENT_SCHEMA
            or row.get("protocol_sha256") != protocol.get("protocol_sha256")
            or row.get("previous_event_sha256") != previous
            or _sha256(row) != claimed_event_hash
        ):
            raise ValueError(f"access ledger hash chain is invalid at row {index + 1}")
        previous = str(claimed_event_hash)
    if (
        events[0].get("event") != "protocol_frozen_test_access_denied"
        or events[1].get("event") != "first_test_access_authorized"
        or authorization.get("previous_access_event_sha256")
        != events[0].get("event_sha256")
    ):
        raise ValueError("access ledger does not bind the test authorization")
    result = {
        "schema_version": "cmd-ghost-live-preflight-v1",
        "decision": "PASS",
        "protocol_sha256": protocol["protocol_sha256"],
        "authorization_sha256": authorization["authorization_sha256"],
        "access_ledger_file_sha256": _file_sha256(access_ledger_path),
        "run_id": run_id,
        "cases_file_sha256": _file_sha256(cases),
        "model_manifest_file_sha256": _file_sha256(model_manifest_path),
        "evaluator_file_sha256": _file_sha256(evaluator_path),
        "preparation_manifest_file_sha256": _file_sha256(
            preparation_manifest_path
        ),
        "candidate_budget": candidate_budget,
        "model_calls": 0,
    }
    return {**result, "preflight_sha256": _sha256(result)}


def _pearson(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or len(left) < 2:
        return 0.0
    left_mean = fmean(left)
    right_mean = fmean(right)
    numerator = sum(
        (x - left_mean) * (y - right_mean) for x, y in zip(left, right, strict=True)
    )
    left_scale = sum((x - left_mean) ** 2 for x in left)
    right_scale = sum((y - right_mean) ** 2 for y in right)
    denominator = math.sqrt(left_scale * right_scale)
    return numerator / denominator if denominator else 0.0


def _family_statistics(
    observations: Sequence[tuple[str, float, float]], *, seed: int, samples: int
) -> tuple[float, float, float, int]:
    grouped: dict[str, list[tuple[float, float]]] = {}
    for family, prior, outcome in observations:
        grouped.setdefault(family, []).append((prior, outcome))
    families = sorted(grouped)
    family_rows = [
        (fmean(row[0] for row in grouped[family]), fmean(row[1] for row in grouped[family]))
        for family in families
    ]
    correlation = _pearson(
        [row[0] for row in family_rows], [row[1] for row in family_rows]
    )
    rng = random.Random(seed)
    bootstrap: list[float] = []
    if len(family_rows) >= 2:
        for _ in range(samples):
            drawn = [family_rows[rng.randrange(len(family_rows))] for _ in family_rows]
            bootstrap.append(
                _pearson([row[0] for row in drawn], [row[1] for row in drawn])
            )
    bootstrap.sort()
    lower = bootstrap[max(0, math.floor(0.05 * len(bootstrap)) - 1)] if bootstrap else 0.0
    concordant = 0
    comparable = 0
    for values in grouped.values():
        for left_index, left in enumerate(values):
            for right in values[left_index + 1 :]:
                prior_delta = left[0] - right[0]
                outcome_delta = left[1] - right[1]
                if prior_delta == 0.0 or outcome_delta == 0.0:
                    continue
                comparable += 1
                concordant += int(prior_delta * outcome_delta > 0.0)
    concordance = concordant / comparable if comparable else 0.0
    return correlation, lower, concordance, len(families)


def audit_feedback(
    input_path: Path,
    output: Path,
    protocol_path: Path,
    selection_path: Path,
    *,
    bootstrap_samples: int = 10_000,
    seed: int = 24,
) -> Mapping[str, object]:
    if bootstrap_samples < 10_000:
        raise ValueError("feedback identifiability requires at least 10000 bootstrap samples")
    protocol = _validated_protocol(protocol_path)
    known = {
        str(row["case_id"]): str(row["partition"])
        for row in protocol["assignments"]
    }
    selections: dict[str, Mapping[str, object]] = {}
    for row in _load_jsonl(selection_path):
        selection_id = row.get("selection_id")
        if (
            row.get("schema_version") != "cmd-ghost-live-selection-v1"
            or not isinstance(selection_id, str)
            or not selection_id
            or selection_id in selections
            or row.get("development_proxy") is not False
        ):
            raise ValueError("prospective selection ledger is invalid")
        selections[selection_id] = row
    rows = _load_jsonl(input_path)
    matured = 0
    censored = 0
    seen: set[str] = set()
    utilities: list[float] = []
    observations: list[tuple[str, float, float]] = []
    matured_selections: set[str] = set()
    required = {
        "schema_version", "feedback_id", "case_id", "selection_id",
        "selected_intent_id", "selected_skill_revision_id", "probe_id",
        "repair_effect", "applicable_signals", "pre_action_prior", "selected_at_utc",
        "window_ends_at_utc", "observed_at_utc", "target_resolved",
        "anchor_non_regression", "recurrence", "annotation_consumed", "valid",
        "rolled_back", "locality_cost", "execution_cost", "provenance",
        "gold_derived", "matured", "development_proxy",
    }
    for raw in rows:
        if set(raw) != required or raw.get("schema_version") != FEEDBACK_SCHEMA:
            raise ValueError("delayed feedback row is not closed or versioned")
        feedback_id = raw.get("feedback_id")
        if not isinstance(feedback_id, str) or not feedback_id or feedback_id in seen:
            raise ValueError("feedback_id must be non-empty and unique")
        seen.add(feedback_id)
        case_id = raw.get("case_id")
        if case_id not in known:
            raise ValueError("feedback references a case outside the frozen protocol")
        selection_id = raw.get("selection_id")
        selection = selections.get(selection_id) if isinstance(selection_id, str) else None
        if selection is None:
            raise ValueError("feedback references an unknown prospective selection")
        bindings = {
            "case_id": "case_id",
            "selected_intent_id": "selected_intent_id",
            "selected_skill_revision_id": "selected_skill_revision_id",
            "probe_id": "probe_id",
            "repair_effect": "repair_effect",
            "pre_action_prior": "pre_action_prior",
        }
        if any(raw[left] != selection[right] for left, right in bindings.items()):
            raise ValueError("feedback disagrees with its prospective selection binding")
        if raw.get("gold_derived") is not False or raw.get("development_proxy") is not False:
            raise ValueError("live delayed feedback cannot be gold-derived or a proxy")
        for key in ("valid", "rolled_back", "matured"):
            if not isinstance(raw.get(key), bool):
                raise ValueError(f"feedback {key} must be boolean")
        effect = raw.get("repair_effect")
        if effect not in EFFECT_SIGNALS:
            raise ValueError("feedback repair_effect is unregistered")
        applicable = raw.get("applicable_signals")
        if not isinstance(applicable, list) or tuple(applicable) != EFFECT_SIGNALS[effect]:
            raise ValueError("feedback signals do not match the skill-conditioned probe")
        prior = raw.get("pre_action_prior")
        if (
            isinstance(prior, bool)
            or not isinstance(prior, (int, float))
            or not math.isfinite(float(prior))
            or not -1.0 <= float(prior) <= 1.0
        ):
            raise ValueError("pre_action_prior must be finite in [-1, 1]")
        selected_at = _timestamp(raw.get("selected_at_utc"), "selected_at_utc")
        window_ends = _timestamp(raw.get("window_ends_at_utc"), "window_ends_at_utc")
        observed_at = _timestamp(raw.get("observed_at_utc"), "observed_at_utc")
        if window_ends <= selected_at or observed_at < selected_at:
            raise ValueError("feedback timestamps violate selection/window chronology")
        locality = raw.get("locality_cost")
        cost = raw.get("execution_cost")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) < 0
            for value in (locality, cost)
        ):
            raise ValueError("feedback costs must be finite and non-negative")
        if raw["matured"] is False:
            if observed_at >= window_ends:
                raise ValueError("feedback past its window cannot remain right-censored")
            if any(raw[key] is not None for key in SIGNALS):
                raise ValueError("right-censored feedback must not fabricate outcomes")
            censored += 1
            continue
        if observed_at < window_ends:
            raise ValueError("feedback cannot mature before its registered window ends")
        if selection_id in matured_selections:
            raise ValueError("selection has more than one matured feedback row")
        matured_selections.add(str(selection_id))
        if any(not isinstance(raw[key], bool) for key in applicable):
            raise ValueError("matured feedback lacks an applicable outcome signal")
        if any(raw[key] is not None for key in set(SIGNALS) - set(applicable)):
            raise ValueError("inapplicable outcome signals must be null")
        positive = {
            "target_resolved": bool(raw["target_resolved"]),
            "anchor_non_regression": bool(raw["anchor_non_regression"]),
            "recurrence": not bool(raw["recurrence"]),
            "annotation_consumed": bool(raw["annotation_consumed"]),
        }
        success = fmean(float(positive[key]) for key in applicable)
        if not raw["valid"] or raw["rolled_back"]:
            utility = -1.0
        else:
            utility = max(-1.0, min(1.0, success - float(locality) - float(cost)))
        utilities.append(utility)
        family_id = next(
            str(row["family_id"])
            for row in protocol["assignments"]
            if row["case_id"] == case_id
        )
        observations.append((family_id, float(prior), utility))
        matured += 1
    correlation, lower, concordance, family_count = _family_statistics(
        observations, seed=seed, samples=bootstrap_samples
    )
    enough_support = matured >= 30 and family_count >= 10
    identifiable = (
        enough_support
        and correlation >= 0.20
        and lower >= 0.10
        and concordance >= 0.55
    )
    report = {
        "schema_version": FEEDBACK_REPORT_SCHEMA,
        "decision": (
            "PASS" if identifiable
            else "BLOCKED_NO_MATURED_FEEDBACK" if matured == 0
            else "BLOCKED_FEEDBACK_NOT_IDENTIFIABLE"
        ),
        "protocol_sha256": protocol["protocol_sha256"],
        "input_file_sha256": _file_sha256(input_path),
        "selection_file_sha256": _file_sha256(selection_path),
        "row_count": len(rows),
        "matured_count": matured,
        "right_censored_count": censored,
        "mean_matured_utility": sum(utilities) / len(utilities) if utilities else None,
        "family_count": family_count,
        "minimum_matured_count": 30,
        "minimum_family_count": 10,
        "family_correlation": correlation,
        "family_correlation_threshold": 0.20,
        "bootstrap_lower_bound": lower,
        "bootstrap_lower_bound_threshold": 0.10,
        "pairwise_concordance": concordance,
        "pairwise_concordance_threshold": 0.55,
        "bootstrap_samples": bootstrap_samples,
        "seed": seed,
        "model_calls": 0,
    }
    result = {**report, "report_sha256": _sha256(report)}
    _atomic_json(output, result)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    freeze_parser = subparsers.add_parser("freeze")
    freeze_parser.add_argument("--root", type=Path, required=True)
    freeze_parser.add_argument("--cases", type=Path, required=True)
    for partition in PARTITIONS:
        freeze_parser.add_argument(f"--{partition.replace('_', '-')}", type=Path, required=True)
    freeze_parser.add_argument("--attestation", type=Path, required=True)
    freeze_parser.add_argument("--model-manifest", type=Path, required=True)
    freeze_parser.add_argument("--evaluator", type=Path, required=True)
    freeze_parser.add_argument("--preparation-manifest", type=Path, required=True)
    freeze_parser.add_argument("--candidate-budget", type=int, required=True)
    freeze_parser.add_argument("--output", type=Path, required=True)
    freeze_parser.add_argument("--access-ledger", type=Path, required=True)
    authorize_parser = subparsers.add_parser("authorize-test")
    authorize_parser.add_argument("--protocol", type=Path, required=True)
    authorize_parser.add_argument("--access-ledger", type=Path, required=True)
    authorize_parser.add_argument("--authorizer", required=True)
    authorize_parser.add_argument("--run-id", required=True)
    authorize_parser.add_argument("--output", type=Path, required=True)
    validate_parser = subparsers.add_parser("validate-run")
    validate_parser.add_argument("--root", type=Path, required=True)
    validate_parser.add_argument("--cases", type=Path, required=True)
    validate_parser.add_argument("--protocol", type=Path, required=True)
    validate_parser.add_argument("--authorization", type=Path, required=True)
    validate_parser.add_argument("--access-ledger", type=Path, required=True)
    validate_parser.add_argument("--model-manifest", type=Path, required=True)
    validate_parser.add_argument("--evaluator", type=Path, required=True)
    validate_parser.add_argument("--preparation-manifest", type=Path, required=True)
    validate_parser.add_argument("--candidate-budget", type=int, required=True)
    validate_parser.add_argument("--run-id", required=True)
    validate_parser.add_argument("--output", type=Path)
    feedback_parser = subparsers.add_parser("audit-feedback")
    feedback_parser.add_argument("--input", type=Path, required=True)
    feedback_parser.add_argument("--protocol", type=Path, required=True)
    feedback_parser.add_argument("--selections", type=Path, required=True)
    feedback_parser.add_argument("--output", type=Path, required=True)
    feedback_parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    feedback_parser.add_argument("--seed", type=int, default=24)
    args = parser.parse_args(argv)
    if args.action == "freeze":
        partition_files = {
            partition: getattr(args, partition) for partition in PARTITIONS
        }
        result = freeze(
            root=args.root,
            cases=args.cases,
            partition_files=partition_files,
            attestation=args.attestation,
            model_manifest=args.model_manifest,
            evaluator=args.evaluator,
            preparation_manifest=args.preparation_manifest,
            output=args.output,
            access_ledger=args.access_ledger,
            candidate_budget=args.candidate_budget,
        )
    elif args.action == "authorize-test":
        result = authorize(
            protocol_path=args.protocol,
            access_ledger=args.access_ledger,
            output=args.output,
            authorizer=args.authorizer,
            run_id=args.run_id,
        )
    elif args.action == "validate-run":
        result = validate_run(
            root=args.root,
            cases=args.cases,
            protocol_path=args.protocol,
            authorization_path=args.authorization,
            access_ledger_path=args.access_ledger,
            model_manifest_path=args.model_manifest,
            evaluator_path=args.evaluator,
            preparation_manifest_path=args.preparation_manifest,
            candidate_budget=args.candidate_budget,
            run_id=args.run_id,
        )
        if args.output is not None:
            _atomic_json(args.output, result)
    else:
        result = audit_feedback(
            args.input,
            args.output,
            args.protocol,
            args.selections,
            bootstrap_samples=args.bootstrap_samples,
            seed=args.seed,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("decision") == "PASS" or "decision" not in result else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ATTESTATION_SCHEMA", "AUTHORIZATION_SCHEMA", "FEEDBACK_SCHEMA",
    "MODEL_MANIFEST_SCHEMA", "PARTITIONS", "PROTOCOL_SCHEMA", "audit_feedback",
    "authorize", "freeze", "main", "validate_run",
]
