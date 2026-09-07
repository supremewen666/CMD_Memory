"""Schema-v2 rejection matrix for successor-v3 F0/F1 validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from cmd_audit.counterfactual.successor_program_ir import (
    IR_GRAMMAR_VERSION,
    REGISTERED_BOUNDS,
    canonical_ast_hash,
    parse_program,
)
from cmd_audit.eval.successor_protocol_freeze import (
    BASELINE_CATALOG_SCHEMA_VERSION,
    COMMAND_LOCATORS,
    DATASET_MANIFEST_SCHEMA_VERSION,
    E0_ENVELOPE_SCHEMA_VERSION,
    FREEZE_SCHEMA_VERSION,
    GENERATION_RULE,
    GLOBAL_LEDGER_GENESIS_SHA256,
    MINIMAL_EFFECTS,
    MINIMAL_LEAVES,
    PROTOCOL_ID,
    REGISTERED_FAMILY_AGGREGATION,
    REGISTERED_SCORE_METRIC,
    REGISTERED_SOURCE_SEMANTICS,
    VALIDATOR_VERSION,
    canonical_json_sha256,
    require_validated_f1,
    sha256_file,
    validate_protocol_freeze,
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _baseline_programs() -> dict[str, dict[str, object]]:
    def rule(predicate: str, action: str) -> dict[str, object]:
        return {"node": "if", "predicate": {"kind": predicate}, "action": {"kind": action}}

    return {
        "B0": {"node": "sequence", "body": []},
        "B1": rule("divergent_pair_member", "annotate_conflict"),
        "B2": rule("superseded_item", "demote"),
        "B3": rule("superseded_item", "suppress"),
        "B4": rule("superseded_item", "replace"),
    }


def _content_hash_without(value: dict[str, Any], field: str) -> str:
    return canonical_json_sha256({key: nested for key, nested in value.items() if key != field})


def _make_fixture(root: Path) -> tuple[dict[str, Any], Path, Path]:
    prompt_path = root / "instrument" / "prompt.txt"
    prompt_path.parent.mkdir(parents=True)
    prompt_path.write_text("frozen symmetric relation prompt", encoding="utf-8")
    source_path = root / "data" / "source.jsonl"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("{}\n", encoding="utf-8")
    access_path = root / "artifacts" / "route_a_successor_v3" / "access.jsonl"
    access_path.parent.mkdir(parents=True)
    access_path.write_text("", encoding="utf-8")

    cases: list[dict[str, Any]] = []
    pairs: list[dict[str, Any]] = []
    templates: list[dict[str, Any]] = []
    split_hashes: dict[str, dict[str, str]] = {}
    for index, split in enumerate(("pilot", "cal", "dev", "search", "query", "deploy_canary")):
        case_id, pair_id = f"case-{split}", f"pair-{split}"
        family_id, template_id = f"family-{split}", f"template-{split}"
        domain_id = f"domain-{index}"
        cases.append({
            "case_id": case_id, "split": split, "family_id": family_id,
            "domain_id": domain_id, "template_ids": [template_id],
            "pair_ids": [pair_id],
        })
        pairs.append({
            "pair_id": pair_id, "case_id": case_id, "left_item_id": f"left-{split}",
            "right_item_id": f"right-{split}", "template_id": template_id,
        })
        templates.append({"template_id": template_id, "family_id": family_id, "domain_id": domain_id})
        split_hashes[split] = {
            "case_ids_sha256": canonical_json_sha256([case_id]),
            "pair_ids_sha256": canonical_json_sha256([pair_id]),
            "family_ids_sha256": canonical_json_sha256([family_id]),
            "template_ids_sha256": canonical_json_sha256([template_id]),
        }
    dataset = {
        "schema_version": DATASET_MANIFEST_SCHEMA_VERSION,
        "created_at": "2026-08-09T00:00:00Z",
        "source_files": [{"path": "data/source.jsonl", "sha256": sha256_file(source_path)}],
        "cases": cases,
        "pairs": pairs,
        "templates": templates,
        "split_hashes": split_hashes,
        "access_log_path": "artifacts/route_a_successor_v3/access.jsonl",
        "access_log_genesis_sha256": GLOBAL_LEDGER_GENESIS_SHA256,
    }
    dataset_path = root / "artifacts" / "route_a_successor_v3" / "dataset_manifest.json"
    _write_json(dataset_path, dataset)

    baseline_path = root / "artifacts" / "route_a_successor_v3" / "baseline" / "baseline_catalog.json"
    baseline_programs = _baseline_programs()
    catalog: dict[str, Any] = {
        "schema_version": BASELINE_CATALOG_SCHEMA_VERSION,
        "protocol_id": PROTOCOL_ID,
        "grammar_version": IR_GRAMMAR_VERSION,
        "baselines": [
            {
                "baseline_id": baseline_id,
                "description": f"registered {baseline_id}",
                "program": program,
                "canonical_ast_sha256": canonical_ast_hash(parse_program(program)),
            }
            for baseline_id, program in baseline_programs.items()
        ],
        "catalog_sha256": "",
    }
    catalog["catalog_sha256"] = _content_hash_without(catalog, "catalog_sha256")
    _write_json(baseline_path, catalog)

    candidate = {
        "node": "sequence",
        "body": [baseline_programs["B1"], baseline_programs["B2"]],
    }
    envelope_path = root / "artifacts" / "route_a_successor_v3" / "e0" / "candidate_envelope.json"
    envelope: dict[str, Any] = {
        "schema_version": E0_ENVELOPE_SCHEMA_VERSION,
        "protocol_id": PROTOCOL_ID,
        "grammar_version": IR_GRAMMAR_VERSION,
        "adaptive": False,
        "generation_rule": GENERATION_RULE,
        "generation_rule_sha256": canonical_json_sha256(GENERATION_RULE),
        "candidates": [{
            "candidate_id": "C0",
            "program": candidate,
            "canonical_ast_sha256": canonical_ast_hash(parse_program(candidate)),
        }],
        "candidate_envelope_sha256": "",
    }
    envelope["candidate_envelope_sha256"] = _content_hash_without(
        envelope, "candidate_envelope_sha256"
    )
    _write_json(envelope_path, envelope)

    commands: dict[str, dict[str, Any]] = {}
    for name, registered in COMMAND_LOCATORS.items():
        script_path = root / str(registered["script"])
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text("def main():\n    return 0\n", encoding="utf-8")
        commands[name] = {**registered, "script_sha256": sha256_file(script_path)}

    freeze: dict[str, Any] = {
        "schema_version": FREEZE_SCHEMA_VERSION,
        "protocol_id": PROTOCOL_ID,
        "freeze_stage": "F1",
        "frozen_at": "2026-08-09T00:00:00Z",
        "dataset_manifest_sha256": sha256_file(dataset_path),
        "splits": split_hashes,
        "instrument": {
            "model_id": "provider/model", "model_revision": "2026-08-01",
            "temperature": 0.0, "top_p": 1.0, "seed": 7,
            "max_output_tokens": 128, "prompt_sha256": sha256_file(prompt_path),
            "parser_version": "v1", "normalization_version": "v1",
            "cache_schema_version": "v1",
        },
        "ordering_policy": {
            "policy_version": "v1",
            "accepted_sources": ["observed_at", "event_sequence", "source_priority"],
            "source_semantics": json.loads(json.dumps(REGISTERED_SOURCE_SEMANTICS)),
            "conflict_policy": "fail_closed",
        },
        "gates": {
            "g0": {
                "metric_version": "v1", "relation_precision_min": 0.8,
                "relation_recall_min": 0.8, "permutation_fpr_max": 0.1,
                "canary_recall_min": 0.8, "abstention_rate_max": 0.2,
                "confidence_level": 0.95, "bootstrap_iterations": 100,
                "bootstrap_seed": 1, "min_pairs": 6, "min_positive_pairs": 2,
                "min_negative_pairs": 2, "min_families": 2,
            },
            "g1": {
                "metric_version": "v1", "target_precision_min": 0.9,
                "target_recall_min": 0.8, "ordering_coverage_min": 0.5,
                "destructive_coverage_min": 0.25, "unknown_rate_max": 0.5,
                "conflict_rate_max": 0.1, "confidence_level": 0.95,
                "bootstrap_iterations": 100, "bootstrap_seed": 2,
                "min_pairs": 6, "min_directional_pairs": 2, "min_families": 2,
            },
            "g2": {
                "metric_version": "v1", "min_firing_cases": 1,
                "min_firing_families": 1, "null_false_fire_max": 0.0,
                "field_alignment_max": 0.5, "nmi_alarm_max": 0.5,
                "permutation_target_precision_max": 0.5,
                "reusable_value_unique_ratio_max": 0.5,
            },
            "g3": {
                "baseline_catalog_path": "artifacts/route_a_successor_v3/baseline/baseline_catalog.json",
                "baseline_catalog_sha256": sha256_file(baseline_path),
            },
            "e0": {
                "candidate_envelope_path": "artifacts/route_a_successor_v3/e0/candidate_envelope.json",
                "candidate_envelope_sha256": sha256_file(envelope_path),
                "score_metric": REGISTERED_SCORE_METRIC,
                "family_aggregation": REGISTERED_FAMILY_AGGREGATION,
                "strict_gain_min": 0.01, "confidence_level": 0.95,
                "bootstrap_iterations": 100, "bootstrap_seed": 3,
                "tie_epsilon": 0.001, "tie_policy": "STOP",
                "missing_policy": "STOP", "nonfinite_policy": "STOP",
            },
        },
        "grammar": {
            "version": IR_GRAMMAR_VERSION, "leaves": list(MINIMAL_LEAVES),
            "effects": list(MINIMAL_EFFECTS), "bounds": REGISTERED_BOUNDS.as_mapping(),
        },
        "budgets": {
            "human_labels": 6, "unique_pair_calls": 6, "retries": 0,
            "e0_candidates": 1, "synthesis_seeds": 1,
            "proposals_per_seed": 1, "query_reads": 1,
        },
        "commands": commands,
        "query_policy": {
            "ledger_path": "artifacts/route_a_successor_v3/query/query_read_ledger.sqlite3",
            "ledger_genesis_sha256": GLOBAL_LEDGER_GENESIS_SHA256,
            "max_reservations": 1, "reservation_consumes_read": True,
        },
        "predecessor_status": {
            "route_a_v1": "E0_STOP_FROZEN", "route_a_v2_slot": "WITHDRAWN",
        },
    }
    return freeze, dataset_path, prompt_path


def test_exact_v2_freeze_passes_and_is_usable_downstream(tmp_path: Path) -> None:
    freeze, dataset_path, prompt_path = _make_fixture(tmp_path)
    report = validate_protocol_freeze(
        freeze, dataset_path=dataset_path, repo_root=tmp_path, prompt_path=prompt_path
    )
    assert report.valid is True, report.reasons
    assert report.validator_version == VALIDATOR_VERSION
    assert report.recomputed_hashes["dataset_manifest_sha256"] == sha256_file(dataset_path)
    assert require_validated_f1(freeze, report.as_dict()) == report.manifest_sha256


def test_freeze_rejects_free_keys_placeholders_and_registry_drift(tmp_path: Path) -> None:
    freeze, dataset_path, prompt_path = _make_fixture(tmp_path)
    freeze["extra"] = "forbidden"
    freeze["instrument"]["model_revision"] = "TBD"  # type: ignore[index]
    freeze["grammar"]["bounds"]["max_depth"] = 99  # type: ignore[index]
    freeze["ordering_policy"]["source_semantics"]["source_priority"]["semantic"] = "lower_wins"  # type: ignore[index]
    report = validate_protocol_freeze(
        freeze, dataset_path=dataset_path, repo_root=tmp_path, prompt_path=prompt_path
    )
    assert "unknown_top_level_key:extra" in report.reasons
    assert "forbidden_value:instrument.model_revision" in report.reasons
    assert "grammar_bounds_not_registered" in report.reasons
    assert "ordering_policy_source_semantics:source_priority" in report.reasons


def _access_row(*, allowed: bool) -> dict[str, Any]:
    row: dict[str, Any] = {
        "seq": 0, "previous_entry_sha256": GLOBAL_LEDGER_GENESIS_SHA256,
        "at": "2026-08-09T00:00:00Z", "actor_id": "tester",
        "command_sha256": "a" * 64, "purpose": "instrument_development",
        "operation": "read", "requested_split": "search",
        "case_ids_sha256": canonical_json_sha256(["case-search"]),
        "allowed": allowed, "result_sha256": "b" * 64, "entry_sha256": "",
    }
    row["entry_sha256"] = _content_hash_without(row, "entry_sha256")
    return row


def test_access_ledger_hash_chain_accepts_denial_but_refuses_allowed_sealed_read(tmp_path: Path) -> None:
    freeze, dataset_path, prompt_path = _make_fixture(tmp_path)
    dataset = json.loads(dataset_path.read_text())
    access_path = tmp_path / dataset["access_log_path"]
    access_path.write_text(json.dumps(_access_row(allowed=False), sort_keys=True) + "\n")
    _write_json(dataset_path, dataset)
    freeze["dataset_manifest_sha256"] = sha256_file(dataset_path)
    report = validate_protocol_freeze(
        freeze, dataset_path=dataset_path, repo_root=tmp_path, prompt_path=prompt_path
    )
    assert report.valid is True, report.reasons

    access_path.write_text(json.dumps(_access_row(allowed=True), sort_keys=True) + "\n")
    report = validate_protocol_freeze(
        freeze, dataset_path=dataset_path, repo_root=tmp_path, prompt_path=prompt_path
    )
    assert "allowed_sealed_split_before_f1:0" in report.reasons


def test_catalog_and_envelope_ast_integrity_are_recomputed(tmp_path: Path) -> None:
    freeze, dataset_path, prompt_path = _make_fixture(tmp_path)
    catalog_path = tmp_path / freeze["gates"]["g3"]["baseline_catalog_path"]
    catalog = json.loads(catalog_path.read_text())
    catalog["baselines"][1]["program"]["action"]["kind"] = "verify"
    _write_json(catalog_path, catalog)
    freeze["gates"]["g3"]["baseline_catalog_sha256"] = sha256_file(catalog_path)
    report = validate_protocol_freeze(
        freeze, dataset_path=dataset_path, repo_root=tmp_path, prompt_path=prompt_path
    )
    assert "baseline_ast_not_exact:B1" in report.reasons
    assert "baseline_ast_hash:B1" in report.reasons
    assert "baseline_catalog_content_hash" in report.reasons


def test_query_policy_uses_schema_genesis_without_creating_mutable_ledger(
    tmp_path: Path,
) -> None:
    freeze, dataset_path, prompt_path = _make_fixture(tmp_path)
    ledger = tmp_path / freeze["query_policy"]["ledger_path"]
    assert not ledger.exists()
    freeze["query_policy"]["ledger_genesis_sha256"] = "0" * 64
    report = validate_protocol_freeze(
        freeze, dataset_path=dataset_path, repo_root=tmp_path, prompt_path=prompt_path
    )
    assert "query_ledger_genesis" in report.reasons
    assert not ledger.exists()
    with pytest.raises(ValueError):
        require_validated_f1(freeze, report.as_dict())
