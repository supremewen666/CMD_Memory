#!/usr/bin/env python3
"""Validate the exact V4 GPU input stream without making model calls."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
import math
from pathlib import Path
from typing import Mapping, Sequence

from cmd_audit.counterfactual.relation_graph import canonical_sha256
from experiments.prepare_v4_live_cases import (
    CACHE_RECORDS_SCHEMA_VERSION,
    GRAPH_ROW_VERSION,
    INSTRUMENT_MANIFEST_SCHEMA_VERSION,
    INTENT_PROPOSAL_ROW_VERSION,
    PREPARATION_SCHEMA_VERSION,
    intent_proposal_cache_key,
    parse_intent_proposals,
    proposer_surface,
)
from experiments.v4_live_materialization import validate_live_input
from experiments.validate_v4_evolution_dataset import validate_bundle


VALIDATION_SCHEMA_VERSION = "cmd-v4-prepared-cases-validation-v1"
_FORBIDDEN_RUNTIME_KEYS = frozenset(
    {
        "gold_answer",
        "gold_evidence",
        "perturbation_label",
        "hidden_intent",
        "allowed_added_item_ids",
        "required_items",
        "required_provenance_hashes",
    }
)
_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "build_status",
        "dataset_sha256",
        "dataset_manifest_file_sha256",
        "instrument_manifest_sha256",
        "relation_cache_sha256",
        "graph_stream_sha256",
        "intent_stream_sha256",
        "prepared_stream_sha256",
        "file_sha256",
        "candidate_budget",
        "chain_pair_budget",
        "selection_mode",
        "selection_limit",
        "source_case_count",
        "eligible_case_count",
        "excluded_no_relation_pair_count",
        "selected_case_count",
        "selected_case_ids",
        "selected_case_ids_sha256",
        "selected_probe_set_counts",
        "relation_request_count",
        "unique_relation_cache_record_count",
        "relation_model_call_count",
        "relation_uncertain_count",
        "relation_uncertain_rate",
        "max_uncertain_rate",
        "relation_counts",
        "actionability_counts",
        "proposer_model_id",
        "proposer_model_sha256",
        "proposer_version",
        "proposer_prompt_template_sha256",
        "proposer_model_call_count",
        "proposer_cache_hit_count",
        "max_proposer_retries",
        "runtime_uses_gold",
        "relation_instrument_uses_gold",
        "intent_proposer_uses_gold",
        "shadow_join_after_graph_and_intent_freeze",
        "preparation_manifest_sha256",
    }
)
_GRAPH_ROW_KEYS = frozenset({"schema_version", "case_id", "graph"})
_INTENT_ROW_KEYS = frozenset(
    {
        "schema_version",
        "case_id",
        "graph_sha256",
        "legacy_intent_id",
        "intents",
        "chain_pairs",
        "proposer_input_sha256",
        "proposer_response_sha256",
        "proposer_response",
        "attempts",
        "proposer_cache_key",
        "proposer_cache_hit",
    }
)
_INSTRUMENT_KEYS = frozenset(
    {
        "schema_version",
        "dataset_sha256",
        "instrument_version",
        "normalization_version",
        "parser_version",
        "prompt_template_sha256",
        "model_id",
        "model_config_sha256",
        "max_uncertain_rate",
        "text_only",
        "direction_free",
        "gold_inputs",
        "instrument_manifest_sha256",
    }
)
_CACHE_RECORD_KEYS = frozenset(
    {
        "cache_key",
        "canonical_left",
        "canonical_right",
        "prompt_sha256",
        "parser_version",
        "model_id",
        "model_config_hash",
        "normalization_version",
        "instrument_version",
        "verdict",
    }
)


def _read_payload(path: Path) -> bytes:
    payload = path.read_bytes()
    return gzip.decompress(payload) if path.suffix == ".gz" else payload


def _load_json(path: Path) -> Mapping[str, object]:
    value = json.loads(_read_payload(path).decode("utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _load_jsonl(path: Path) -> list[Mapping[str, object]]:
    rows: list[Mapping[str, object]] = []
    for line_number, line in enumerate(
        _read_payload(path).decode("utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, Mapping):
            raise ValueError(f"{path.name}:{line_number} must be a JSON object")
        rows.append(value)
    return rows


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _valid_integer(value: object, *, minimum: int = 0) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= minimum


def _valid_rate(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and 0 <= value <= 1
    )


def _keys(value: object):
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key)
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def _portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(resolved)


def _report(
    *,
    dataset_dir: Path,
    prepared_path: Path,
    reasons: Sequence[str],
    summary: Mapping[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "decision": "PASS" if not reasons else "REFUSE",
        "dataset_dir": _portable_path(dataset_dir),
        "prepared_path": _portable_path(prepared_path),
        "reasons": sorted(set(reasons)),
        "summary": dict(summary or {}),
        "model_calls": 0,
    }
    return {**payload, "report_sha256": canonical_sha256(payload)}


def validate_prepared_cases(
    *,
    dataset_dir: Path,
    prepared_path: Path,
    manifest_path: Path,
) -> dict[str, object]:
    """Replay source, graph, intent, hash, and leakage bindings fail closed."""
    dataset_dir = Path(dataset_dir)
    prepared_path = Path(prepared_path)
    manifest_path = Path(manifest_path)
    reasons: list[str] = []
    dataset_report = validate_bundle(dataset_dir)
    if dataset_report.get("decision") != "PASS":
        reasons.append("cpu_dataset_validation_not_pass")
    try:
        manifest = _load_json(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return _report(
            dataset_dir=dataset_dir,
            prepared_path=prepared_path,
            reasons=(f"manifest_input_error:{type(error).__name__}",),
        )
    if set(manifest) != _MANIFEST_KEYS:
        reasons.append("preparation_manifest_not_closed")
    if (
        manifest.get("schema_version") != PREPARATION_SCHEMA_VERSION
        or manifest.get("build_status") != "gpu_input_ready"
        or manifest.get("runtime_uses_gold") is not False
        or manifest.get("relation_instrument_uses_gold") is not False
        or manifest.get("intent_proposer_uses_gold") is not False
        or manifest.get("shadow_join_after_graph_and_intent_freeze") is not True
    ):
        reasons.append("preparation_manifest_contract")
    integer_fields = {
        "candidate_budget": 1,
        "chain_pair_budget": 0,
        "source_case_count": 0,
        "eligible_case_count": 0,
        "excluded_no_relation_pair_count": 0,
        "selected_case_count": 1,
        "relation_request_count": 1,
        "unique_relation_cache_record_count": 1,
        "relation_model_call_count": 0,
        "relation_uncertain_count": 0,
        "proposer_model_call_count": 0,
        "proposer_cache_hit_count": 0,
        "max_proposer_retries": 0,
    }
    if (
        any(
            not _valid_integer(manifest.get(name), minimum=minimum)
            for name, minimum in integer_fields.items()
        )
        or (
            manifest.get("selection_limit") is not None
            and not _valid_integer(manifest.get("selection_limit"), minimum=1)
        )
        or not _valid_rate(manifest.get("relation_uncertain_rate"))
        or not _valid_rate(manifest.get("max_uncertain_rate"))
    ):
        reasons.append("preparation_manifest_numeric_contract")
    claimed_manifest_hash = manifest.get("preparation_manifest_sha256")
    manifest_body = {
        key: value
        for key, value in manifest.items()
        if key != "preparation_manifest_sha256"
    }
    if claimed_manifest_hash != canonical_sha256(manifest_body):
        reasons.append("preparation_manifest_hash_mismatch")
    if not prepared_path.is_file():
        reasons.append("prepared_stream_missing")
        return _report(
            dataset_dir=dataset_dir,
            prepared_path=prepared_path,
            reasons=reasons,
        )
    if manifest.get("prepared_stream_sha256") != _file_sha256(prepared_path):
        reasons.append("prepared_stream_hash_mismatch")
    file_hashes = manifest.get("file_sha256")
    artifacts_dir = manifest_path.parent
    expected_artifacts = {
        "instrument_manifest.json": artifacts_dir / "instrument_manifest.json",
        "relation_cache_records.jsonl": artifacts_dir / "relation_cache_records.jsonl",
        "graphs.jsonl": artifacts_dir / "graphs.jsonl",
        "intent_proposals.jsonl": artifacts_dir / "intent_proposals.jsonl",
        "prepared_cases.jsonl": prepared_path,
    }
    if not isinstance(file_hashes, Mapping) or set(file_hashes) != set(
        expected_artifacts
    ):
        reasons.append("preparation_file_hashes_not_closed")
        file_hashes = {}
    for name, path in expected_artifacts.items():
        if not path.is_file():
            reasons.append(f"preparation_artifact_missing:{name}")
        elif file_hashes.get(name) != _file_sha256(path):
            reasons.append(f"preparation_artifact_hash_mismatch:{name}")
    if any(reason.startswith("preparation_artifact_missing:") for reason in reasons):
        return _report(
            dataset_dir=dataset_dir,
            prepared_path=prepared_path,
            reasons=reasons,
        )

    try:
        dataset_manifest = _load_json(dataset_dir / "dataset_manifest.json")
        runtime_rows = _load_jsonl(dataset_dir / "runtime_cases.jsonl.gz")
        shadow_rows = _load_jsonl(dataset_dir / "shadow_cases.jsonl.gz")
        relation_rows = _load_jsonl(dataset_dir / "relation_requests.jsonl.gz")
        prepared_rows = _load_jsonl(prepared_path)
        graph_rows = _load_jsonl(artifacts_dir / "graphs.jsonl")
        intent_rows = _load_jsonl(artifacts_dir / "intent_proposals.jsonl")
        cache_records = _load_jsonl(artifacts_dir / "relation_cache_records.jsonl")
        instrument_manifest = _load_json(artifacts_dir / "instrument_manifest.json")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        reasons.append(f"prepared_input_error:{type(error).__name__}")
        return _report(
            dataset_dir=dataset_dir,
            prepared_path=prepared_path,
            reasons=reasons,
        )

    if manifest.get("dataset_sha256") != dataset_manifest.get("dataset_sha256"):
        reasons.append("prepared_dataset_binding_mismatch")
    if manifest.get("dataset_manifest_file_sha256") != _file_sha256(
        dataset_dir / "dataset_manifest.json"
    ):
        reasons.append("prepared_dataset_manifest_file_hash_mismatch")
    if manifest.get("instrument_manifest_sha256") != instrument_manifest.get(
        "instrument_manifest_sha256"
    ):
        reasons.append("instrument_manifest_binding_mismatch")
    if (
        set(instrument_manifest) != _INSTRUMENT_KEYS
        or instrument_manifest.get("schema_version")
        != INSTRUMENT_MANIFEST_SCHEMA_VERSION
        or instrument_manifest.get("text_only") is not True
        or instrument_manifest.get("direction_free") is not True
        or instrument_manifest.get("gold_inputs") is not False
    ):
        reasons.append("instrument_manifest_contract")
    instrument_body = {
        key: value
        for key, value in instrument_manifest.items()
        if key != "instrument_manifest_sha256"
    }
    if instrument_manifest.get("instrument_manifest_sha256") != canonical_sha256(
        instrument_body
    ):
        reasons.append("instrument_manifest_hash_mismatch")
    if instrument_manifest.get("dataset_sha256") != manifest.get("dataset_sha256"):
        reasons.append("instrument_dataset_binding_mismatch")
    if instrument_manifest.get("max_uncertain_rate") != manifest.get(
        "max_uncertain_rate"
    ):
        reasons.append("instrument_uncertainty_cutoff_mismatch")
    cache_record_hashes: set[str] = set()
    for record in cache_records:
        if set(record) != _CACHE_RECORD_KEYS or not isinstance(
            record.get("verdict"), Mapping
        ):
            reasons.append("relation_cache_record_not_closed")
            continue
        digest = canonical_sha256(record)
        if digest in cache_record_hashes:
            reasons.append("duplicate_relation_cache_record")
        cache_record_hashes.add(digest)
    cache_payload = {
        "schema_version": CACHE_RECORDS_SCHEMA_VERSION,
        "instrument_manifest_sha256": manifest.get("instrument_manifest_sha256"),
        "records": cache_records,
    }
    if manifest.get("relation_cache_sha256") != canonical_sha256(cache_payload):
        reasons.append("relation_cache_logical_hash_mismatch")
    if manifest.get("unique_relation_cache_record_count") != len(cache_records):
        reasons.append("relation_cache_record_count_mismatch")
    if manifest.get("graph_stream_sha256") != _file_sha256(
        artifacts_dir / "graphs.jsonl"
    ):
        reasons.append("graph_stream_binding_mismatch")
    if manifest.get("intent_stream_sha256") != _file_sha256(
        artifacts_dir / "intent_proposals.jsonl"
    ):
        reasons.append("intent_stream_binding_mismatch")

    runtime_by_id = {str(row.get("case_id")): row for row in runtime_rows}
    shadow_by_id = {str(row.get("case_id")): row for row in shadow_rows}
    graph_by_id = {str(row.get("case_id")): row for row in graph_rows}
    intent_by_id = {str(row.get("case_id")): row for row in intent_rows}
    for row in graph_rows:
        if (
            set(row) != _GRAPH_ROW_KEYS
            or row.get("schema_version") != GRAPH_ROW_VERSION
        ):
            reasons.append("graph_row_not_closed")
    for row in intent_rows:
        if (
            set(row) != _INTENT_ROW_KEYS
            or row.get("schema_version") != INTENT_PROPOSAL_ROW_VERSION
        ):
            reasons.append("intent_proposal_row_not_closed")
    relation_by_case: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in relation_rows:
        relation_by_case[str(row.get("case_id"))].append(row)
    source_ids = set(runtime_by_id)
    eligible_ids = set(relation_by_case)
    if manifest.get("source_case_count") != len(source_ids):
        reasons.append("source_case_count_mismatch")
    if manifest.get("eligible_case_count") != len(eligible_ids):
        reasons.append("eligible_case_count_mismatch")
    if manifest.get("excluded_no_relation_pair_count") != len(
        source_ids - eligible_ids
    ):
        reasons.append("excluded_no_relation_pair_count_mismatch")
    selected_ids = manifest.get("selected_case_ids")
    if (
        not isinstance(selected_ids, list)
        or not all(isinstance(value, str) for value in selected_ids)
        or len(set(selected_ids)) != len(selected_ids)
    ):
        reasons.append("selected_case_ids_invalid")
        selected_ids = []
    if manifest.get("selected_case_ids_sha256") != canonical_sha256(selected_ids):
        reasons.append("selected_case_ids_hash_mismatch")
    if set(selected_ids) - eligible_ids:
        reasons.append("selected_case_without_relation_pair")
    prepared_ids = [str(row.get("case_id")) for row in prepared_rows]
    if prepared_ids != selected_ids:
        reasons.append("prepared_case_order_or_coverage_mismatch")
    if manifest.get("selected_case_count") != len(prepared_rows):
        reasons.append("prepared_case_count_mismatch")
    expected_selected_relation_count = sum(
        len(relation_by_case[case_id]) for case_id in selected_ids
    )
    if manifest.get("relation_request_count") != expected_selected_relation_count:
        reasons.append("selected_relation_request_count_mismatch")
    if len(graph_by_id) != len(graph_rows) or set(graph_by_id) != set(selected_ids):
        reasons.append("graph_case_coverage_mismatch")
    if len(intent_by_id) != len(intent_rows) or set(intent_by_id) != set(selected_ids):
        reasons.append("intent_case_coverage_mismatch")

    event_indexes: list[int] = []
    relation_counts: Counter[str] = Counter()
    actionability_counts: Counter[str] = Counter()
    proposer_attempt_sum = 0
    proposer_cache_hit_sum = 0
    for row in prepared_rows:
        case_id = str(row.get("case_id"))
        try:
            frozen = validate_live_input(row)
        except (KeyError, TypeError, ValueError) as error:
            reasons.append(f"live_input_invalid:{case_id}:{type(error).__name__}")
            continue
        event_indexes.append(frozen.context.event_index)
        if len(frozen.intents) != manifest.get("candidate_budget"):
            reasons.append(f"candidate_budget_mismatch:{case_id}")
        if len(frozen.chain_pairs) != manifest.get("chain_pair_budget"):
            reasons.append(f"chain_pair_budget_mismatch:{case_id}")
        runtime_source = runtime_by_id.get(case_id)
        shadow_source = shadow_by_id.get(case_id)
        if runtime_source is None or shadow_source is None:
            reasons.append(f"prepared_source_case_missing:{case_id}")
            continue
        if row.get("runtime_case") != runtime_source.get("runtime_case"):
            reasons.append(f"runtime_source_binding_mismatch:{case_id}")
        if (
            row.get("probe_case") != shadow_source.get("probe_case")
            or row.get("family_id") != shadow_source.get("family_id")
            or row.get("probe_set") != shadow_source.get("probe_set")
        ):
            reasons.append(f"shadow_source_binding_mismatch:{case_id}")
        graph_row = graph_by_id.get(case_id, {})
        intent_row = intent_by_id.get(case_id, {})
        if row.get("graph") != graph_row.get("graph"):
            reasons.append(f"graph_artifact_binding_mismatch:{case_id}")
        if (
            row.get("intents") != intent_row.get("intents")
            or row.get("legacy_intent_id") != intent_row.get("legacy_intent_id")
            or row.get("chain_pairs") != intent_row.get("chain_pairs")
        ):
            reasons.append(f"intent_artifact_binding_mismatch:{case_id}")
        if intent_row.get("graph_sha256") != frozen.graph.graph_sha256:
            reasons.append(f"intent_graph_binding_mismatch:{case_id}")
        candidate_budget = manifest.get("candidate_budget")
        if (
            not isinstance(candidate_budget, bool)
            and isinstance(candidate_budget, int)
            and candidate_budget > 1
        ):
            try:
                expected_surface = proposer_surface(
                    runtime_source["runtime_case"],
                    frozen.graph,
                    proposals_needed=candidate_budget - 1,
                )
                if intent_row.get("proposer_input_sha256") != canonical_sha256(
                    expected_surface
                ):
                    reasons.append(f"proposer_input_binding_mismatch:{case_id}")
                expected_cache_key = intent_proposal_cache_key(
                    expected_surface,
                    proposer_model_hash=manifest["proposer_model_sha256"],
                    proposals_needed=candidate_budget - 1,
                )
                if intent_row.get("proposer_cache_key") != expected_cache_key:
                    reasons.append(f"proposer_cache_key_mismatch:{case_id}")
                response = intent_row.get("proposer_response")
                if not isinstance(response, Mapping):
                    raise ValueError("proposer response is not a mapping")
                if intent_row.get("proposer_response_sha256") != canonical_sha256(
                    response
                ):
                    reasons.append(f"proposer_response_hash_mismatch:{case_id}")
                replayed = parse_intent_proposals(
                    json.dumps(
                        response,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    graph=frozen.graph,
                    proposals_needed=candidate_budget - 1,
                    proposer_model_hash=manifest["proposer_model_sha256"],
                )
                if [intent.to_mapping() for intent in replayed] != intent_row.get(
                    "intents", []
                )[1:]:
                    reasons.append(f"proposer_response_binding_mismatch:{case_id}")
                attempts = intent_row.get("attempts")
                cache_hit = intent_row.get("proposer_cache_hit")
                valid_attempts = (
                    isinstance(cache_hit, bool)
                    and isinstance(attempts, int)
                    and not isinstance(attempts, bool)
                    and (
                        (cache_hit and attempts == 0)
                        or (
                            not cache_hit
                            and 1
                            <= attempts
                            <= int(manifest.get("max_proposer_retries", -1)) + 1
                        )
                    )
                )
                if not valid_attempts:
                    reasons.append(f"proposer_attempt_count_invalid:{case_id}")
                else:
                    proposer_attempt_sum += attempts
                    proposer_cache_hit_sum += cache_hit
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                reasons.append(f"proposer_response_binding_mismatch:{case_id}")
        runtime_surface = {
            key: value
            for key, value in row.items()
            if key not in {"probe_case", "family_id", "probe_set"}
        }
        leaked_keys = sorted(set(_keys(runtime_surface)) & _FORBIDDEN_RUNTIME_KEYS)
        if leaked_keys:
            reasons.append(f"runtime_shadow_key_leak:{case_id}:{','.join(leaked_keys)}")
        evaluation_family = row.get("family_id")
        if isinstance(evaluation_family, str) and evaluation_family in json.dumps(
            runtime_surface, ensure_ascii=False, sort_keys=True
        ):
            reasons.append(f"runtime_evaluation_family_leak:{case_id}")
        for edge in frozen.graph.edges:
            relation_counts[edge.relation] += 1
            actionability_counts[edge.actionability.mode.value] += 1
            if edge.measurement.instrument_manifest_sha256 != manifest.get(
                "instrument_manifest_sha256"
            ):
                reasons.append(f"graph_instrument_binding_mismatch:{case_id}")
            if edge.measurement.cache_record_sha256 not in cache_record_hashes:
                reasons.append(f"graph_cache_record_missing:{case_id}")
            if frozen.graph.cache_manifest_sha256 != manifest.get(
                "relation_cache_sha256"
            ):
                reasons.append(f"graph_cache_binding_mismatch:{case_id}")

    if event_indexes != sorted(event_indexes) or len(set(event_indexes)) != len(
        event_indexes
    ):
        reasons.append("prepared_event_indexes_not_strictly_increasing")
    probe_counts = Counter(str(row.get("probe_set")) for row in prepared_rows)
    if manifest.get("selected_probe_set_counts") != dict(sorted(probe_counts.items())):
        reasons.append("selected_probe_set_counts_mismatch")
    if manifest.get("relation_counts") != dict(sorted(relation_counts.items())):
        reasons.append("relation_counts_mismatch")
    if manifest.get("actionability_counts") != dict(
        sorted(actionability_counts.items())
    ):
        reasons.append("actionability_counts_mismatch")
    uncertain_raw = manifest.get("relation_uncertain_count")
    relation_raw = manifest.get("relation_request_count")
    uncertain_count = uncertain_raw if _valid_integer(uncertain_raw) else -1
    relation_count = relation_raw if _valid_integer(relation_raw, minimum=1) else -1
    expected_uncertain_rate = (
        uncertain_count / relation_count if relation_count > 0 else 0.0
    )
    if manifest.get("relation_uncertain_rate") != expected_uncertain_rate:
        reasons.append("relation_uncertain_rate_mismatch")
    max_uncertain = manifest.get("max_uncertain_rate")
    max_uncertain_value = float(max_uncertain) if _valid_rate(max_uncertain) else -1.0
    if expected_uncertain_rate > max_uncertain_value:
        reasons.append("relation_uncertain_rate_exceeds_cutoff")
    if uncertain_count != relation_counts.get("uncertain", 0):
        reasons.append("relation_uncertain_count_mismatch")
    if manifest.get("proposer_model_call_count") != proposer_attempt_sum:
        reasons.append("proposer_model_call_count_mismatch")
    if manifest.get("proposer_cache_hit_count") != proposer_cache_hit_sum:
        reasons.append("proposer_cache_hit_count_mismatch")

    summary = {
        "source_case_count": len(source_ids),
        "eligible_case_count": len(eligible_ids),
        "excluded_no_relation_pair_count": len(source_ids - eligible_ids),
        "prepared_case_count": len(prepared_rows),
        "candidate_budget": manifest.get("candidate_budget"),
        "probe_set_counts": dict(sorted(probe_counts.items())),
        "relation_counts": dict(sorted(relation_counts.items())),
        "actionability_counts": dict(sorted(actionability_counts.items())),
        "runtime_shadow_leak_count": sum(
            reason.startswith("runtime_shadow_key_leak:")
            or reason.startswith("runtime_evaluation_family_leak:")
            for reason in reasons
        ),
    }
    return _report(
        dataset_dir=dataset_dir,
        prepared_path=prepared_path,
        reasons=reasons,
        summary=summary,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = validate_prepared_cases(
            dataset_dir=args.dataset_dir,
            prepared_path=args.prepared,
            manifest_path=args.manifest,
        )
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        report = _report(
            dataset_dir=args.dataset_dir,
            prepared_path=args.prepared,
            reasons=(f"input_error:{type(error).__name__}",),
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"decision": report["decision"], "output": str(args.output)}))
    return 0 if report["decision"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["VALIDATION_SCHEMA_VERSION", "main", "validate_prepared_cases"]
