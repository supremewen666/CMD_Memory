"""Machine-readable run, decision-log, and receipt-log schema validators."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping

from .contracts import canonical_sha256


@dataclass(frozen=True)
class RunManifest:
    run_id: str
    stage: str
    protocol_version: str
    source_audit_sha256: str
    split_manifest_sha256: str
    lockbox_manifest_sha256: str
    router_name: str
    router_initial_state_sha256: str
    model_id: str
    budget: Mapping[str, int | float]
    dry_run: bool
    content_sha256: str

    @classmethod
    def create(cls, **kwargs: object) -> "RunManifest":
        body = dict(kwargs)
        required = {
            "run_id", "stage", "protocol_version", "source_audit_sha256",
            "split_manifest_sha256", "lockbox_manifest_sha256", "router_name",
            "router_initial_state_sha256", "model_id", "budget", "dry_run",
        }
        if set(body) != required:
            raise ValueError("run manifest uses a closed schema")
        return cls(**body, content_sha256=canonical_sha256(body))  # type: ignore[arg-type]

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)


def validate_decision_log(value: Mapping[str, object]) -> None:
    stage5_required = {
        "schema_version", "router_name", "case_id", "selected_skill_revision_id",
        "candidate_skill_revision_ids", "backbone_prediction_sha256",
        "router_state_before_sha256", "selection_id", "selection_mode", "scores",
    }
    runtime_required = stage5_required | {"prediction_source"}
    schema_version = value.get("schema_version")
    if schema_version == "cmd-spec-v03-router-decision-v1":
        required = stage5_required
    elif schema_version == "cmd-spec-v03-runtime-router-decision-v1":
        required = runtime_required
    else:
        raise ValueError("decision log uses an unsupported schema")
    if set(value) != required:
        raise ValueError("decision log uses an unsupported schema")
    if not isinstance(value["candidate_skill_revision_ids"], (list, tuple)) or not value["candidate_skill_revision_ids"]:
        raise ValueError("decision log requires a non-empty candidate list")
    if schema_version == "cmd-spec-v03-runtime-router-decision-v1" and value["prediction_source"] not in {"external_backbone", "development_zero_backbone"}:
        raise ValueError("decision log has an invalid prediction source")


def validate_receipt_log(value: Mapping[str, object]) -> None:
    required = {
        "schema_version", "receipt_id", "decision_id", "case_id", "outcome",
        "before_state_hash", "shadow_state_hash", "committed_state_hash",
        "invariants_passed", "safety_passed", "locality_passed", "settled_at_event",
    }
    if set(value) != required or value.get("schema_version") != "cmd-spec-v03-receipt-v1":
        raise ValueError("receipt log uses an unsupported schema")
    if value["outcome"] not in {"commit", "rollback", "abstain"}:
        raise ValueError("receipt log outcome is invalid")
