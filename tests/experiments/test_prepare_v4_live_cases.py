from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cmd_audit.counterfactual.relation_graph import canonical_sha256
from experiments.build_v4_evolution_dataset import build_dataset
import experiments.prepare_v4_live_cases as preparation_module
from experiments.prepare_v4_live_cases import prepare_live_cases
from experiments.v4_live_materialization import validate_live_input
from experiments.validate_v4_prepared_cases import (
    main as validate_prepared_main,
    validate_prepared_cases,
)


ROOT = Path(__file__).resolve().parents[2]
PROBE_DIR = ROOT / "data" / "probe_cases"


class PositiveRelationJudge:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        assert "gold" not in prompt.casefold()
        self.calls += 1
        return json.dumps({"relation": "same_slot_different_value", "slot": "fact"})

    def generate_json(
        self,
        prompt: str,
        *,
        schema: object,
        schema_name: str,
        system: str | None = None,
    ) -> str:
        assert schema_name == "slot_relation"
        return self.generate(prompt, system=system)


class CompleteIntentJudge:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        assert "gold" not in prompt.casefold()
        assert "family_id" not in prompt
        payload = json.loads(prompt.split("PROPOSER_INPUT:\n", 1)[1])
        edge = payload["edges"][0]
        actionability = edge["actionability"]
        self.calls += 1
        if actionability["mode"] == "destructive":
            proposals = [
                {
                    "strategy_id": "prefer_trusted_later_fact_v1",
                    "relation_edge_id": edge["edge_id"],
                    "effect": "demote",
                    "target_item_id": actionability["target_item_id"],
                    "replacement_item_id": None,
                },
                {
                    "strategy_id": "replace_superseded_with_survivor_v1",
                    "relation_edge_id": edge["edge_id"],
                    "effect": "replace",
                    "target_item_id": actionability["target_item_id"],
                    "replacement_item_id": actionability["survivor_item_id"],
                },
                {
                    "strategy_id": "verify_before_semantic_change_v1",
                    "relation_edge_id": edge["edge_id"],
                    "effect": "verify",
                    "target_item_id": None,
                    "replacement_item_id": None,
                },
            ]
        else:
            proposals = [
                {
                    "strategy_id": "annotate_semantic_disagreement_v1",
                    "relation_edge_id": edge["edge_id"],
                    "effect": "annotate_conflict",
                    "target_item_id": None,
                    "replacement_item_id": None,
                },
                {
                    "strategy_id": "verify_semantic_relation_v1",
                    "relation_edge_id": edge["edge_id"],
                    "effect": "verify",
                    "target_item_id": None,
                    "replacement_item_id": None,
                },
                {
                    "strategy_id": "abstain_on_unsafe_direction_v1",
                    "relation_edge_id": edge["edge_id"],
                    "effect": "abstain",
                    "target_item_id": None,
                    "replacement_item_id": None,
                },
            ]
        return json.dumps({"proposals": proposals})


class LeakingIntentJudge(CompleteIntentJudge):
    def generate(self, prompt: str, *, system: str | None = None) -> str:
        value = json.loads(super().generate(prompt, system=system))
        value["proposals"][0]["strategy_id"] = "gold_case_target_strategy_v1"
        return json.dumps(value)


class MalformedRelationJudge:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        self.calls += 1
        return "not-json"

    def generate_json(
        self,
        prompt: str,
        *,
        schema: object,
        schema_name: str,
        system: str | None = None,
    ) -> str:
        return self.generate(prompt, system=system)


class RetryingRelationJudge(PositiveRelationJudge):
    def generate(self, prompt: str, *, system: str | None = None) -> str:
        assert "gold" not in prompt.casefold()
        self.calls += 1
        if self.calls % 2:
            return '{"relation":"unrelated","reason":"extra"}'
        return '```json\n{"relation":"same_slot_different_value","slot":"fact"}\n```'


def _dataset(tmp_path: Path) -> Path:
    output = tmp_path / "dataset"
    build_dataset(
        source_paths={
            "memtrace_kp": PROBE_DIR / "memtrace_kp_cases.json",
            "stale_item": PROBE_DIR / "stale_item_cases.json",
            "memfail": PROBE_DIR / "memfail_cases.json",
        },
        output_dir=output,
        limit_per_domain=12,
    )
    return output


def _rows(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_valid_frozen_inputs_produce_gpu_loadable_prepared_cases(
    tmp_path: Path,
) -> None:
    dataset = _dataset(tmp_path)
    output = tmp_path / "prepared_cases.jsonl"
    artifacts = tmp_path / "preparation"
    manifest = prepare_live_cases(
        dataset_dir=dataset,
        output_path=output,
        artifacts_dir=artifacts,
        cache_path=tmp_path / "relation_cache.sqlite",
        relation_judge=PositiveRelationJudge(),
        intent_judge=CompleteIntentJudge(),
        instrument_model_id="relation-model-v1",
        instrument_model_hash="a" * 64,
        proposer_model_id="intent-model-v1",
        proposer_model_hash="b" * 64,
        candidate_budget=4,
        limit=2,
    )

    report = validate_prepared_cases(
        dataset_dir=dataset,
        prepared_path=output,
        manifest_path=artifacts / "preparation_manifest.json",
    )
    rows = _rows(output)

    assert manifest["build_status"] == "gpu_input_ready"
    assert report["decision"] == "PASS", report["reasons"]
    assert len(rows) == 2
    assert all(validate_live_input(row).case_id == row["case_id"] for row in rows)
    assert all(len(row["intents"]) == 4 for row in rows)
    assert all("gold_answer" in row["probe_case"] for row in rows)
    runtime_surfaces = [
        {
            key: value
            for key, value in row.items()
            if key not in {"probe_case", "family_id", "probe_set"}
        }
        for row in rows
    ]
    assert "gold_answer" not in json.dumps(runtime_surfaces, ensure_ascii=False)
    assert manifest["excluded_no_relation_pair_count"] >= 0
    assert manifest["candidate_budget"] == 4
    assert manifest["selected_probe_set_counts"] == {
        "represented": 1,
        "unseen": 1,
    }
    assert manifest["relation_cache_sha256"]
    assert manifest["graph_stream_sha256"]
    assert manifest["intent_stream_sha256"]


def test_relation_cache_replay_makes_zero_repeated_relation_calls(
    tmp_path: Path,
) -> None:
    dataset = _dataset(tmp_path)
    cache = tmp_path / "relation_cache.sqlite"
    first_relation_judge = PositiveRelationJudge()
    first_intent_judge = CompleteIntentJudge()
    first_manifest = prepare_live_cases(
        dataset_dir=dataset,
        output_path=tmp_path / "first.jsonl",
        artifacts_dir=tmp_path / "first-artifacts",
        cache_path=cache,
        relation_judge=first_relation_judge,
        intent_judge=first_intent_judge,
        instrument_model_id="relation-model-v1",
        instrument_model_hash="a" * 64,
        proposer_model_id="intent-model-v1",
        proposer_model_hash="b" * 64,
        candidate_budget=4,
        limit=2,
    )
    replay_relation_judge = PositiveRelationJudge()
    replay_intent_judge = CompleteIntentJudge()
    replay_manifest = prepare_live_cases(
        dataset_dir=dataset,
        output_path=tmp_path / "replay.jsonl",
        artifacts_dir=tmp_path / "replay-artifacts",
        cache_path=cache,
        relation_judge=replay_relation_judge,
        intent_judge=replay_intent_judge,
        instrument_model_id="relation-model-v1",
        instrument_model_hash="a" * 64,
        proposer_model_id="intent-model-v1",
        proposer_model_hash="b" * 64,
        candidate_budget=4,
        limit=2,
    )

    assert first_relation_judge.calls > 0
    assert first_intent_judge.calls > 0
    assert replay_relation_judge.calls == 0
    assert replay_intent_judge.calls == 0
    assert replay_manifest["relation_model_call_count"] == 0
    assert replay_manifest["proposer_model_call_count"] == 0
    assert (
        replay_manifest["relation_cache_sha256"]
        == first_manifest["relation_cache_sha256"]
    )
    assert (tmp_path / "replay.jsonl").read_bytes() == (
        tmp_path / "first.jsonl"
    ).read_bytes()


def test_relation_attempt_ledger_binds_structured_retries_and_raw_responses(
    tmp_path: Path,
) -> None:
    dataset = _dataset(tmp_path)
    artifacts = tmp_path / "artifacts"
    judge = RetryingRelationJudge()

    manifest = prepare_live_cases(
        dataset_dir=dataset,
        output_path=tmp_path / "prepared.jsonl",
        artifacts_dir=artifacts,
        cache_path=tmp_path / "relation_cache.sqlite",
        relation_judge=judge,
        intent_judge=CompleteIntentJudge(),
        instrument_model_id="relation-model-v2",
        instrument_model_hash="a" * 64,
        proposer_model_id="intent-model-v1",
        proposer_model_hash="b" * 64,
        candidate_budget=4,
        max_relation_attempts=3,
        limit=2,
    )
    attempts = _rows(artifacts / "relation_responses.jsonl")
    report = validate_prepared_cases(
        dataset_dir=dataset,
        prepared_path=tmp_path / "prepared.jsonl",
        manifest_path=artifacts / "preparation_manifest.json",
    )

    assert report["decision"] == "PASS", report["reasons"]
    assert manifest["relation_model_call_count"] == judge.calls == len(attempts)
    assert manifest["relation_attempt_count"] == len(attempts)
    assert manifest["relation_retry_count"] * 2 == len(attempts)
    assert manifest["max_relation_attempts"] == 3
    assert manifest["relation_reason_counts"] == {
        "accepted_fenced_json": len(attempts) // 2,
        "invalid_schema": len(attempts) // 2,
    }
    assert all(row["structured_output_used"] is True for row in attempts)
    for row in attempts:
        raw = row["raw_response"]
        assert row["raw_response_sha256"] == hashlib.sha256(raw.encode()).hexdigest()
        assert row["response_record_sha256"] == canonical_sha256(
            {
                key: value
                for key, value in row.items()
                if key != "response_record_sha256"
            }
        )


def test_validator_refuses_rehashed_evaluation_family_leak_in_runtime_context(
    tmp_path: Path,
) -> None:
    dataset = _dataset(tmp_path)
    output = tmp_path / "prepared.jsonl"
    artifacts = tmp_path / "artifacts"
    prepare_live_cases(
        dataset_dir=dataset,
        output_path=output,
        artifacts_dir=artifacts,
        cache_path=tmp_path / "relation_cache.sqlite",
        relation_judge=PositiveRelationJudge(),
        intent_judge=CompleteIntentJudge(),
        instrument_model_id="relation-model-v1",
        instrument_model_hash="a" * 64,
        proposer_model_id="intent-model-v1",
        proposer_model_hash="b" * 64,
        candidate_budget=4,
        limit=2,
    )
    rows = _rows(output)
    rows[0]["context"]["domain"] = rows[0]["family_id"]
    output.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    manifest_path = artifacts / "preparation_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    prepared_sha = hashlib.sha256(output.read_bytes()).hexdigest()
    manifest["prepared_stream_sha256"] = prepared_sha
    manifest["file_sha256"]["prepared_cases.jsonl"] = prepared_sha
    manifest["preparation_manifest_sha256"] = canonical_sha256(
        {
            key: value
            for key, value in manifest.items()
            if key != "preparation_manifest_sha256"
        }
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    report = validate_prepared_cases(
        dataset_dir=dataset,
        prepared_path=output,
        manifest_path=manifest_path,
    )

    assert report["decision"] == "REFUSE"
    assert any(
        reason.startswith("runtime_evaluation_family_leak:")
        for reason in report["reasons"]
    )


def test_preparation_refuses_proposer_strategy_identity_leakage(
    tmp_path: Path,
) -> None:
    dataset = _dataset(tmp_path)

    with pytest.raises(ValueError, match="exhausted closed-schema retries"):
        prepare_live_cases(
            dataset_dir=dataset,
            output_path=tmp_path / "prepared.jsonl",
            artifacts_dir=tmp_path / "artifacts",
            cache_path=tmp_path / "relation_cache.sqlite",
            relation_judge=PositiveRelationJudge(),
            intent_judge=LeakingIntentJudge(),
            instrument_model_id="relation-model-v1",
            instrument_model_hash="a" * 64,
            proposer_model_id="intent-model-v1",
            proposer_model_hash="b" * 64,
            candidate_budget=4,
            limit=2,
        )

    assert not (tmp_path / "prepared.jsonl").exists()
    assert not (tmp_path / "artifacts" / "preparation_manifest.json").exists()


def test_preparation_refuses_relation_uncertainty_above_frozen_cutoff(
    tmp_path: Path,
) -> None:
    dataset = _dataset(tmp_path)
    malformed = MalformedRelationJudge()
    artifacts = tmp_path / "artifacts"
    cache = tmp_path / "relation_cache.sqlite"

    with pytest.raises(ValueError, match="uncertainty rate exceeds frozen cutoff"):
        prepare_live_cases(
            dataset_dir=dataset,
            output_path=tmp_path / "prepared.jsonl",
            artifacts_dir=artifacts,
            cache_path=cache,
            relation_judge=malformed,
            intent_judge=CompleteIntentJudge(),
            instrument_model_id="relation-model-v1",
            instrument_model_hash="a" * 64,
            proposer_model_id="intent-model-v1",
            proposer_model_hash="b" * 64,
            candidate_budget=4,
            max_uncertain_rate=0.05,
            limit=2,
        )

    assert not (tmp_path / "prepared.jsonl").exists()
    assert not (artifacts / "preparation_manifest.json").exists()
    attempts = _rows(artifacts / "relation_responses.jsonl")
    failure_report = json.loads(
        (artifacts / "relation_measurement_report.json").read_text(encoding="utf-8")
    )
    assert len(attempts) == malformed.calls
    assert failure_report["decision"] == "REFUSE"
    assert failure_report["reason_counts"] == {"malformed_json": len(attempts)}

    recovered = PositiveRelationJudge()
    recovered_manifest = prepare_live_cases(
        dataset_dir=dataset,
        output_path=tmp_path / "prepared.jsonl",
        artifacts_dir=artifacts,
        cache_path=cache,
        relation_judge=recovered,
        intent_judge=CompleteIntentJudge(),
        instrument_model_id="relation-model-v1",
        instrument_model_hash="a" * 64,
        proposer_model_id="intent-model-v1",
        proposer_model_hash="b" * 64,
        candidate_budget=4,
        max_uncertain_rate=0.05,
        limit=2,
    )
    assert recovered.calls > 0
    assert recovered_manifest["relation_uncertain_count"] == 0


def test_validator_refuses_rehashed_proposer_ledger_drift(
    tmp_path: Path,
) -> None:
    dataset = _dataset(tmp_path)
    output = tmp_path / "prepared.jsonl"
    artifacts = tmp_path / "artifacts"
    prepare_live_cases(
        dataset_dir=dataset,
        output_path=output,
        artifacts_dir=artifacts,
        cache_path=tmp_path / "relation_cache.sqlite",
        relation_judge=PositiveRelationJudge(),
        intent_judge=CompleteIntentJudge(),
        instrument_model_id="relation-model-v1",
        instrument_model_hash="a" * 64,
        proposer_model_id="intent-model-v1",
        proposer_model_hash="b" * 64,
        candidate_budget=4,
        limit=2,
    )
    ledger_path = artifacts / "intent_proposals.jsonl"
    ledger = _rows(ledger_path)
    ledger[0]["proposer_response"]["proposals"][0]["strategy_id"] = (
        "different_reusable_strategy_v1"
    )
    ledger_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for row in ledger
        ),
        encoding="utf-8",
    )
    manifest_path = artifacts / "preparation_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    ledger_sha = hashlib.sha256(ledger_path.read_bytes()).hexdigest()
    manifest["intent_stream_sha256"] = ledger_sha
    manifest["file_sha256"]["intent_proposals.jsonl"] = ledger_sha
    manifest["preparation_manifest_sha256"] = canonical_sha256(
        {
            key: value
            for key, value in manifest.items()
            if key != "preparation_manifest_sha256"
        }
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    report = validate_prepared_cases(
        dataset_dir=dataset,
        prepared_path=output,
        manifest_path=manifest_path,
    )

    assert report["decision"] == "REFUSE"
    assert any(
        reason.startswith("proposer_response_binding_mismatch:")
        for reason in report["reasons"]
    )


def test_prepare_and_validate_cli_seams_emit_ready_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = _dataset(tmp_path)
    output = tmp_path / "prepared.jsonl"
    artifacts = tmp_path / "artifacts"
    validation = tmp_path / "validation.json"
    monkeypatch.setenv("LLM_JUDGE_MODEL", "relation-model-v1")
    monkeypatch.setenv("LLM_MODEL", "intent-model-v1")

    def client(config):
        return (
            PositiveRelationJudge()
            if config.model == "relation-model-v1"
            else CompleteIntentJudge()
        )

    monkeypatch.setattr(preparation_module, "LLMClient", client)

    assert (
        preparation_module.main(
            (
                "--dataset-dir",
                str(dataset),
                "--output",
                str(output),
                "--artifacts-dir",
                str(artifacts),
                "--cache",
                str(tmp_path / "cache.sqlite"),
                "--limit",
                "2",
            )
        )
        == 0
    )
    assert (
        validate_prepared_main(
            (
                "--dataset-dir",
                str(dataset),
                "--prepared",
                str(output),
                "--manifest",
                str(artifacts / "preparation_manifest.json"),
                "--output",
                str(validation),
            )
        )
        == 0
    )
    assert json.loads(validation.read_text(encoding="utf-8"))["decision"] == "PASS"


def test_validator_returns_refuse_for_adversarial_manifest_numeric_types(
    tmp_path: Path,
) -> None:
    dataset = _dataset(tmp_path)
    output = tmp_path / "prepared.jsonl"
    artifacts = tmp_path / "artifacts"
    prepare_live_cases(
        dataset_dir=dataset,
        output_path=output,
        artifacts_dir=artifacts,
        cache_path=tmp_path / "cache.sqlite",
        relation_judge=PositiveRelationJudge(),
        intent_judge=CompleteIntentJudge(),
        instrument_model_id="relation-model-v1",
        instrument_model_hash="a" * 64,
        proposer_model_id="intent-model-v1",
        proposer_model_hash="b" * 64,
        candidate_budget=4,
        limit=2,
    )
    manifest_path = artifacts / "preparation_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["relation_request_count"] = "not-an-integer"
    manifest["preparation_manifest_sha256"] = canonical_sha256(
        {
            key: value
            for key, value in manifest.items()
            if key != "preparation_manifest_sha256"
        }
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    report = validate_prepared_cases(
        dataset_dir=dataset,
        prepared_path=output,
        manifest_path=manifest_path,
    )

    assert report["decision"] == "REFUSE"
    assert "preparation_manifest_numeric_contract" in report["reasons"]


def test_validator_refuses_malformed_rehashed_relation_attempt_without_throwing(
    tmp_path: Path,
) -> None:
    dataset = _dataset(tmp_path)
    output = tmp_path / "prepared.jsonl"
    artifacts = tmp_path / "artifacts"
    prepare_live_cases(
        dataset_dir=dataset,
        output_path=output,
        artifacts_dir=artifacts,
        cache_path=tmp_path / "cache.sqlite",
        relation_judge=PositiveRelationJudge(),
        intent_judge=CompleteIntentJudge(),
        instrument_model_id="relation-model-v2",
        instrument_model_hash="a" * 64,
        proposer_model_id="intent-model-v1",
        proposer_model_hash="b" * 64,
        candidate_budget=4,
        limit=2,
    )
    response_path = artifacts / "relation_responses.jsonl"
    responses = _rows(response_path)
    responses[0]["attempt_index"] = "not-an-integer"
    responses[0]["response_record_sha256"] = canonical_sha256(
        {
            key: value
            for key, value in responses[0].items()
            if key != "response_record_sha256"
        }
    )
    response_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for row in responses
        ),
        encoding="utf-8",
    )
    report_path = artifacts / "relation_measurement_report.json"
    measurement_report = json.loads(report_path.read_text(encoding="utf-8"))
    measurement_report["response_stream_sha256"] = canonical_sha256(responses)
    measurement_report["report_sha256"] = canonical_sha256(
        {
            key: value
            for key, value in measurement_report.items()
            if key != "report_sha256"
        }
    )
    report_path.write_text(
        json.dumps(measurement_report, ensure_ascii=False, sort_keys=True, indent=2)
        + "\n",
        encoding="utf-8",
    )
    manifest_path = artifacts / "preparation_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    response_file_sha = hashlib.sha256(response_path.read_bytes()).hexdigest()
    report_file_sha = hashlib.sha256(report_path.read_bytes()).hexdigest()
    manifest["relation_response_stream_sha256"] = response_file_sha
    manifest["relation_measurement_report_sha256"] = report_file_sha
    manifest["file_sha256"]["relation_responses.jsonl"] = response_file_sha
    manifest["file_sha256"]["relation_measurement_report.json"] = report_file_sha
    manifest["preparation_manifest_sha256"] = canonical_sha256(
        {
            key: value
            for key, value in manifest.items()
            if key != "preparation_manifest_sha256"
        }
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    validation = validate_prepared_cases(
        dataset_dir=dataset,
        prepared_path=output,
        manifest_path=manifest_path,
    )

    assert validation["decision"] == "REFUSE"
    assert "relation_response_attempt_index" in validation["reasons"]
