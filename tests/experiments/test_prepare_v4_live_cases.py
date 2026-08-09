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

    def generate_json(
        self,
        prompt: str,
        *,
        schema: object,
        schema_name: str,
        system: str | None = None,
    ) -> str:
        assert schema_name == "repair_intents"
        return self.generate(prompt, system=system)


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


class RetryingIntentJudge(CompleteIntentJudge):
    def __init__(self) -> None:
        super().__init__()
        self.schema_hashes: list[str] = []

    def generate_json(
        self,
        prompt: str,
        *,
        schema: object,
        schema_name: str,
        system: str | None = None,
    ) -> str:
        assert schema_name == "repair_intents"
        self.schema_hashes.append(canonical_sha256(schema))
        if len(self.schema_hashes) % 2:
            self.calls += 1
            return "not-json"
        return f"```json\n{self.generate(prompt, system=system)}\n```"


class DuplicateUntilCorrectedIntentJudge(CompleteIntentJudge):
    def generate(self, prompt: str, *, system: str | None = None) -> str:
        response = json.loads(super().generate(prompt, system=system))
        if "duplicate concrete intents" not in prompt:
            response["proposals"] = [response["proposals"][0]] * 3
        return json.dumps(response)


class RenamedDuplicateActionIntentJudge(CompleteIntentJudge):
    def generate(self, prompt: str, *, system: str | None = None) -> str:
        response = json.loads(super().generate(prompt, system=system))
        repeated = response["proposals"][0]
        response["proposals"] = [
            {**repeated, "strategy_id": f"renamed_duplicate_action_v{index}"}
            for index in range(1, 4)
        ]
        return json.dumps(response)


class MalformedIntentJudge:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        raise AssertionError("plain generation must not be used")

    def generate_json(
        self,
        prompt: str,
        *,
        schema: object,
        schema_name: str,
        system: str | None = None,
    ) -> str:
        self.calls += 1
        return "Here are the repair intents: not-json"


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


def test_intent_schema_encodes_actionability_instead_of_only_identifier_enums() -> None:
    surface = {
        "proposals_needed": 2,
        "retrieved_items": [
            {"item_id": "old"},
            {"item_id": "new"},
            {"item_id": "other"},
        ],
        "edges": [
            {
                "edge_id": "directed",
                "actionability": {
                    "mode": "destructive",
                    "target_item_id": "old",
                    "survivor_item_id": "new",
                },
            },
            {
                "edge_id": "unknown",
                "actionability": {
                    "mode": "annotate_only",
                    "target_item_id": None,
                    "survivor_item_id": None,
                },
            },
        ],
    }

    schema = preparation_module.intent_response_schema(surface)
    branches = schema["properties"]["proposals"]["items"]["oneOf"]
    directed = [
        branch
        for branch in branches
        if branch["properties"]["relation_edge_id"] == {"const": "directed"}
    ]
    unknown = [
        branch
        for branch in branches
        if branch["properties"]["relation_edge_id"] == {"const": "unknown"}
    ]

    assert {tuple(branch["properties"]["effect"]["enum"]) for branch in directed} == {
        ("abstain", "annotate_conflict", "verify"),
        ("demote", "suppress"),
        ("replace",),
    }
    assert len(unknown) == 1
    assert unknown[0]["properties"]["effect"]["enum"] == [
        "abstain",
        "annotate_conflict",
        "verify",
    ]
    assert unknown[0]["properties"]["target_item_id"] == {"const": None}
    assert unknown[0]["properties"]["replacement_item_id"] == {"const": None}
    replace = next(
        branch
        for branch in directed
        if branch["properties"]["effect"]["enum"] == ["replace"]
    )
    assert replace["properties"]["target_item_id"] == {"const": "old"}
    assert replace["properties"]["replacement_item_id"] == {"const": "new"}


def test_intent_schema_avoids_unsupported_vllm_unique_items_keyword() -> None:
    surface = {
        "proposals_needed": 3,
        "retrieved_items": [{"item_id": "old"}, {"item_id": "new"}],
        "edges": [
            {
                "edge_id": "unknown",
                "actionability": {
                    "mode": "abstain",
                    "target_item_id": None,
                    "survivor_item_id": None,
                },
            }
        ],
    }

    schema = preparation_module.intent_response_schema(surface)
    proposals = schema["properties"]["proposals"]

    assert proposals["minItems"] == proposals["maxItems"] == 3
    assert "uniqueItems" not in proposals


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
    replay_validation = validate_prepared_cases(
        dataset_dir=dataset,
        prepared_path=tmp_path / "replay.jsonl",
        manifest_path=tmp_path / "replay-artifacts" / "preparation_manifest.json",
    )

    assert first_relation_judge.calls > 0
    assert first_intent_judge.calls > 0
    assert replay_relation_judge.calls == 0
    assert replay_intent_judge.calls == 0
    assert replay_manifest["relation_model_call_count"] == 0
    assert replay_manifest["proposer_model_call_count"] == 0
    assert replay_validation["decision"] == "PASS", replay_validation["reasons"]
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


def test_intent_attempt_ledger_binds_structured_retries_and_fenced_json(
    tmp_path: Path,
) -> None:
    dataset = _dataset(tmp_path)
    artifacts = tmp_path / "artifacts"
    proposer = RetryingIntentJudge()

    manifest = prepare_live_cases(
        dataset_dir=dataset,
        output_path=tmp_path / "prepared.jsonl",
        artifacts_dir=artifacts,
        cache_path=tmp_path / "cache.sqlite",
        relation_judge=PositiveRelationJudge(),
        intent_judge=proposer,
        instrument_model_id="relation-model-v2",
        instrument_model_hash="a" * 64,
        proposer_model_id="intent-model-v2",
        proposer_model_hash="b" * 64,
        candidate_budget=4,
        max_proposer_retries=2,
        limit=2,
    )
    attempts = _rows(artifacts / "intent_responses.jsonl")
    proposal_report = json.loads(
        (artifacts / "intent_proposal_report.json").read_text(encoding="utf-8")
    )
    validation = validate_prepared_cases(
        dataset_dir=dataset,
        prepared_path=tmp_path / "prepared.jsonl",
        manifest_path=artifacts / "preparation_manifest.json",
    )

    assert validation["decision"] == "PASS", validation["reasons"]
    assert manifest["proposer_model_call_count"] == proposer.calls == len(attempts)
    assert manifest["proposer_attempt_count"] == len(attempts)
    assert manifest["proposer_retry_count"] == len(attempts) // 2
    assert manifest["proposer_reason_counts"] == {
        "accepted_fenced_json": len(attempts) // 2,
        "malformed_json": len(attempts) // 2,
    }
    assert proposal_report["decision"] == "PASS"
    assert all(row["structured_output_used"] is True for row in attempts)
    assert len(set(proposer.schema_hashes)) == 2


def test_duplicate_intent_response_is_corrected_with_audited_retry(
    tmp_path: Path,
) -> None:
    dataset = _dataset(tmp_path)
    artifacts = tmp_path / "artifacts"
    proposer = DuplicateUntilCorrectedIntentJudge()

    manifest = prepare_live_cases(
        dataset_dir=dataset,
        output_path=tmp_path / "prepared.jsonl",
        artifacts_dir=artifacts,
        cache_path=tmp_path / "cache.sqlite",
        relation_judge=PositiveRelationJudge(),
        intent_judge=proposer,
        instrument_model_id="relation-model-v2",
        instrument_model_hash="a" * 64,
        proposer_model_id="intent-model-v3",
        proposer_model_hash="b" * 64,
        candidate_budget=4,
        max_proposer_retries=2,
        limit=2,
    )

    report = json.loads(
        (artifacts / "intent_proposal_report.json").read_text(encoding="utf-8")
    )
    assert manifest["build_status"] == "gpu_input_ready"
    assert report["decision"] == "PASS"
    assert report["reason_counts"] == {"accepted": 2, "invalid_schema": 2}
    assert report["model_call_count"] == proposer.calls == 4


def test_strategy_renaming_cannot_disguise_duplicate_concrete_actions(
    tmp_path: Path,
) -> None:
    dataset = _dataset(tmp_path)
    artifacts = tmp_path / "artifacts"
    proposer = RenamedDuplicateActionIntentJudge()

    with pytest.raises(
        ValueError,
        match="intent proposer exhausted closed-schema retries: invalid_schema",
    ):
        prepare_live_cases(
            dataset_dir=dataset,
            output_path=tmp_path / "prepared.jsonl",
            artifacts_dir=artifacts,
            cache_path=tmp_path / "cache.sqlite",
            relation_judge=PositiveRelationJudge(),
            intent_judge=proposer,
            instrument_model_id="relation-model-v2",
            instrument_model_hash="a" * 64,
            proposer_model_id="intent-model-v3",
            proposer_model_hash="b" * 64,
            candidate_budget=4,
            max_proposer_retries=2,
            limit=2,
        )

    report = json.loads(
        (artifacts / "intent_proposal_report.json").read_text(encoding="utf-8")
    )
    assert report["decision"] == "REFUSE"
    assert report["reason_counts"] == {"invalid_schema": 3}


def test_exhausted_intent_json_failure_keeps_audit_and_allows_clean_rerun(
    tmp_path: Path,
) -> None:
    dataset = _dataset(tmp_path)
    artifacts = tmp_path / "artifacts"
    cache = tmp_path / "cache.sqlite"
    malformed = MalformedIntentJudge()

    with pytest.raises(
        ValueError,
        match="intent proposer exhausted closed-schema retries: malformed_json",
    ):
        prepare_live_cases(
            dataset_dir=dataset,
            output_path=tmp_path / "prepared.jsonl",
            artifacts_dir=artifacts,
            cache_path=cache,
            relation_judge=PositiveRelationJudge(),
            intent_judge=malformed,
            instrument_model_id="relation-model-v2",
            instrument_model_hash="a" * 64,
            proposer_model_id="intent-model-v2",
            proposer_model_hash="b" * 64,
            candidate_budget=4,
            max_proposer_retries=2,
            limit=2,
        )

    attempts = _rows(artifacts / "intent_responses.jsonl")
    failure_report = json.loads(
        (artifacts / "intent_proposal_report.json").read_text(encoding="utf-8")
    )
    assert malformed.calls == len(attempts) == 3
    assert failure_report["decision"] == "REFUSE"
    assert failure_report["reason_counts"] == {"malformed_json": 3}
    assert not (artifacts / "preparation_manifest.json").exists()
    assert not (tmp_path / "prepared.jsonl").exists()

    recovered = CompleteIntentJudge()
    manifest = prepare_live_cases(
        dataset_dir=dataset,
        output_path=tmp_path / "prepared.jsonl",
        artifacts_dir=artifacts,
        cache_path=cache,
        relation_judge=PositiveRelationJudge(),
        intent_judge=recovered,
        instrument_model_id="relation-model-v2",
        instrument_model_hash="a" * 64,
        proposer_model_id="intent-model-v2",
        proposer_model_hash="b" * 64,
        candidate_budget=4,
        max_proposer_retries=2,
        limit=2,
    )
    assert manifest["build_status"] == "gpu_input_ready"
    assert recovered.calls > 0


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
