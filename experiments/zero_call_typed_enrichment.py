"""Development-only typed enrichment of legacy V4 materialized cases.

This module never constructs a model client.  It re-executes the frozen repair
programs locally and copies legacy recovery fields only into the isolated shadow
reference portion of the resulting case.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Mapping, Sequence

from cmd_audit.counterfactual.repair_state import initial_state_from_runtime_case
from cmd_audit.counterfactual.successor_state_executor import execute_program
from experiments.v4_live_materialization import validate_live_input, _changed_item_ids
from experiments.v4_prequential_runner import CASE_SCHEMA_VERSION, V4CandidateOutcome

ENRICHMENT_SCHEMA_VERSION = "cmd-v4-zero-call-typed-enrichment-v1-development-only"
FROZEN_PREPARED_SHA256 = "0b1b13ac255382433c37711585760e7d7842b3fe03b5fbe9124fa6f12bb9a94e"
FROZEN_LEGACY_SHA256 = "2866229eeb9dc1224caa4bbc9e7197ff8a209bb2c169663c1c57dddb9e512f2e"
FROZEN_LEGACY_CASE_COUNT = 543


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_ids(row: Mapping[str, object]) -> tuple[str, ...]:
    return tuple(sorted(str(item["intent_id"]) for item in row["intents"]))


def _bind_rows(prepared: Mapping[str, object], legacy: Mapping[str, object]) -> None:
    for key in ("case_id", "family_id", "probe_set", "legacy_intent_id"):
        if prepared.get(key) != legacy.get(key):
            raise ValueError(f"prepared/legacy binding mismatch: {key}")
    if _canonical_ids(prepared) != _canonical_ids(legacy):
        raise ValueError("prepared/legacy intent IDs mismatch")
    if _hash(prepared["intents"]) != _hash(legacy["intents"]):
        raise ValueError("prepared/legacy intent payload hash mismatch")
    for key in ("context", "graph"):
        if _hash(prepared[key]) != _hash(legacy[key]):
            raise ValueError(f"prepared/legacy {key} hash mismatch")


def enrich_row(prepared: Mapping[str, object], legacy: Mapping[str, object]) -> dict[str, object]:
    _bind_rows(prepared, legacy)
    frozen = validate_live_input(prepared)
    initial = initial_state_from_runtime_case(frozen.runtime_case)
    raw_legacy_outcomes = legacy["candidate_outcomes"]
    if not isinstance(raw_legacy_outcomes, list):
        raise ValueError("legacy candidate outcomes must be a list")
    legacy_outcomes = {
        str(row["intent_id"]): row
        for row in raw_legacy_outcomes
    }
    if (
        len(raw_legacy_outcomes) != len(frozen.intents)
        or len(legacy_outcomes) != len(raw_legacy_outcomes)
        or set(legacy_outcomes) != {intent.intent_id for intent in frozen.intents}
    ):
        raise ValueError("legacy outcomes do not exactly cover intents")
    outcomes: list[V4CandidateOutcome] = []
    for intent in frozen.intents:
        edge = next(row for row in frozen.graph.edges if row.edge_id == intent.relation_edge_id)
        state = execute_program(
            __import__("cmd_audit.repair.parametric_policy", fromlist=["compile_intent"]).compile_intent(intent, graph=frozen.graph),
            frozen.runtime_case, initial, graph=frozen.graph,
            expected_graph_sha256=frozen.graph.graph_sha256,
            expected_protocol_manifest_sha256=frozen.graph.protocol_manifest_sha256,
        ).state
        changed_ids = tuple(sorted(_changed_item_ids(initial, state)))
        old = legacy_outcomes[intent.intent_id]
        V4CandidateOutcome.from_mapping(old)
        local_count = len(changed_ids)
        local_locality = local_count / max(1, len(state.items))
        # Validity is a runtime execution fact, not a legacy answer/shadow
        # label.  Recompute it from the local state so typed feedback and E4
        # never consume the old materialization's outcome flags.
        local_valid = state.token_count <= frozen.runtime_case.token_budget
        local_rolled_back = not local_valid
        mismatch = (
            local_count != int(old["changed_item_count"])
            or abs(local_locality - float(old["locality_cost"])) > 1e-9
            or local_valid != bool(old["valid"])
            or local_rolled_back != bool(old["rolled_back"])
        )
        destructive = intent.effect in {"replace", "demote", "suppress"}
        if not destructive:
            binding = None
            target_match = None
        else:
            binding = (
                None
                if intent.target_item_id is None
                else intent.target_item_id == edge.actionability.target_item_id
            )
            target_match = (
                None
                if binding is None
                else bool(binding and intent.target_item_id in changed_ids)
            )
        provenance = {
            "schema_version": ENRICHMENT_SCHEMA_VERSION,
            "case_id": frozen.case_id,
            "graph_sha256": frozen.graph.graph_sha256,
            "initial_state_sha256": initial.state_hash,
            "executed_state_sha256": state.state_hash,
            "changed_item_ids_sha256": _hash(changed_ids),
            "source": "local-zero-call-state-diff",
            "mismatch": mismatch,
        }
        outcomes.append(V4CandidateOutcome(
            intent.intent_id, float(old["recovery_gain"]), local_locality, local_count, local_valid, local_rolled_back,
            actionability_mode_observed=edge.actionability.mode.value,
            target_binding_observed=binding,
            target_match_observed=target_match,
            changed_item_ids=changed_ids,
            typed_evidence_provenance=provenance,
        ))
    result = dict(legacy)
    result["schema_version"] = CASE_SCHEMA_VERSION
    result["candidate_outcomes"] = [row.to_mapping() for row in outcomes]
    # Keep the row a closed V4PrequentialCase.  Enrichment bookkeeping belongs
    # in the sidecar manifest; putting it here makes the typed rows impossible
    # to consume through the closed E2/E4 loader.
    return result


def enrich_files(prepared_path: Path, legacy_path: Path, output: Path, manifest_path: Path, *, legacy_manifest_path: Path | None = None, limit: int | None = None) -> dict[str, object]:
    if output.exists() or manifest_path.exists():
        raise ValueError("refusing to overwrite enrichment output or manifest")
    prepared_hash = _file_hash(prepared_path)
    legacy_hash = _file_hash(legacy_path)
    if prepared_hash != FROZEN_PREPARED_SHA256:
        raise ValueError("prepared source hash is not the frozen development input")
    if legacy_hash != FROZEN_LEGACY_SHA256:
        raise ValueError("legacy source hash is not the frozen materialized reference")
    prepared = [json.loads(line) for line in prepared_path.read_text().splitlines() if line.strip()]
    legacy = [json.loads(line) for line in legacy_path.read_text().splitlines() if line.strip()]
    if len(prepared) != FROZEN_LEGACY_CASE_COUNT or len(legacy) != FROZEN_LEGACY_CASE_COUNT:
        raise ValueError("frozen prepared/legacy case count mismatch")
    legacy_by_id = {row["case_id"]: row for row in legacy}
    if len(legacy_by_id) != len(legacy) or {row["case_id"] for row in prepared} != set(legacy_by_id):
        raise ValueError("prepared/legacy case IDs are not a one-to-one closed binding")
    if legacy_manifest_path is None:
        legacy_manifest_path = Path(str(legacy_path) + ".manifest.json")
    if not legacy_manifest_path.exists():
        raise ValueError("frozen legacy manifest is required")
    legacy_manifest = json.loads(legacy_manifest_path.read_text())
    if legacy_manifest.get("output_sha256") != legacy_hash or legacy_manifest.get("case_count") != FROZEN_LEGACY_CASE_COUNT:
        raise ValueError("legacy manifest does not bind the frozen reference")
    rows = []
    mismatch = 0
    for source in prepared[:limit]:
        row = enrich_row(source, legacy_by_id[source["case_id"]])
        mismatch += int(any(
            bool(outcome.get("typed_evidence_provenance", {}).get("mismatch"))
            for outcome in row["candidate_outcomes"]
        ))
        rows.append(row)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n", encoding="utf-8")
    legacy_manifest_hash = _file_hash(legacy_manifest_path)
    historical_calls: object = "UNVERIFIED"
    historical_calls = legacy_manifest.get("model_call_accounting", "UNVERIFIED")
    manifest = {"schema_version": ENRICHMENT_SCHEMA_VERSION, "prepared_sha256": prepared_hash, "legacy_sha256": legacy_hash, "legacy_manifest_sha256": legacy_manifest_hash, "output_sha256": _file_hash(output), "case_count": len(rows), "source_case_count": len(prepared), "intent_count": sum(len(row["intents"]) for row in rows), "mismatch_case_count": mismatch, "model_calls_new": 0, "historical_upstream_calls": historical_calls, "reference_is_fresh_replay": False, "development_only": True, "not_confirmatory_reason": "legacy recovery fields are shadow reference and no real follow-up events exist", "provenance_sha256": _hash({"prepared_sha256": prepared_hash, "legacy_sha256": legacy_hash, "output_sha256": _file_hash(output)})}
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--legacy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--legacy-manifest", type=Path)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    legacy_manifest = args.legacy_manifest or Path(str(args.legacy) + ".manifest.json")
    print(json.dumps(enrich_files(args.prepared, args.legacy, args.output, args.manifest, legacy_manifest_path=legacy_manifest, limit=args.limit), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
