from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

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


def _slotted_proposals(schema: object) -> dict[str, dict[str, object]]:
    slots = schema["properties"]["proposals"]["properties"]
    proposals = {}
    for index, (slot_name, slot_schema) in enumerate(slots.items(), 1):
        branch = slot_schema["oneOf"][0]
        properties = branch["properties"]
        proposals[slot_name] = {
            "strategy_id": f"slotted_semantic_action_v{index}",
            "relation_edge_id": properties["relation_edge_id"]["const"],
            "effect": properties["effect"]["const"],
            "target_item_id": properties["target_item_id"]["const"],
            "replacement_item_id": properties["replacement_item_id"]["const"],
        }
    return proposals


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
        assert "gold" not in prompt.casefold()
        assert "family_id" not in prompt
        self.calls += 1
        return json.dumps({"proposals": _slotted_proposals(schema)})


class LeakingIntentJudge(CompleteIntentJudge):
    def generate_json(
        self,
        prompt: str,
        *,
        schema: object,
        schema_name: str,
        system: str | None = None,
    ) -> str:
        value = json.loads(
            super().generate_json(
                prompt,
                schema=schema,
                schema_name=schema_name,
                system=system,
            )
        )
        value["proposals"]["candidate_1"]["strategy_id"] = (
            "gold_case_target_strategy_v1"
        )
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
        response = super().generate_json(
            prompt,
            schema=schema,
            schema_name=schema_name,
            system=system,
        )
        return f"```json\n{response}\n```"


class DuplicateUntilCorrectedIntentJudge(CompleteIntentJudge):
    def generate_json(
        self,
        prompt: str,
        *,
        schema: object,
        schema_name: str,
        system: str | None = None,
    ) -> str:
        response = json.loads(
            super().generate_json(
                prompt,
                schema=schema,
                schema_name=schema_name,
                system=system,
            )
        )
        if "duplicate concrete intents" not in prompt:
            repeated = response["proposals"]["candidate_1"]
            response["proposals"] = {
                slot_name: repeated for slot_name in response["proposals"]
            }
        return json.dumps(response)


class RenamedDuplicateActionIntentJudge(CompleteIntentJudge):
    def generate_json(
        self,
        prompt: str,
        *,
        schema: object,
        schema_name: str,
        system: str | None = None,
    ) -> str:
        response = json.loads(
            super().generate_json(
                prompt,
                schema=schema,
                schema_name=schema_name,
                system=system,
            )
        )
        repeated = response["proposals"]["candidate_1"]
        response["proposals"] = {
            slot_name: {
                **repeated,
                "strategy_id": f"renamed_duplicate_action_v{index}",
            }
            for index, slot_name in enumerate(response["proposals"], 1)
        }
        return json.dumps(response)


class SlottedIntentJudge:
    def __init__(self) -> None:
        self.calls = 0

    def generate_json(
        self,
        prompt: str,
        *,
        schema: object,
        schema_name: str,
        system: str | None = None,
    ) -> str:
        assert schema_name == "repair_intents"
        assert "gold" not in prompt.casefold()
        self.calls += 1
        return json.dumps({"proposals": _slotted_proposals(schema)})


class CompilerLeakUntilCorrectedIntentJudge(CompleteIntentJudge):
    def generate_json(
        self,
        prompt: str,
        *,
        schema: object,
        schema_name: str,
        system: str | None = None,
    ) -> str:
        response = json.loads(
            super().generate_json(
                prompt,
                schema=schema,
                schema_name=schema_name,
                system=system,
            )
        )
        if "strategy_identifier_uses_forbidden_token" not in prompt:
            response["proposals"]["candidate_1"]["strategy_id"] = (
                "gold_specific_strategy_v1"
            )
        return json.dumps(response)


class LegacyCacheCorrectionIntentJudge(CompleteIntentJudge):
    def generate_json(
        self,
        prompt: str,
        *,
        schema: object,
        schema_name: str,
        system: str | None = None,
    ) -> str:
        assert "strategy_identifier_uses_forbidden_token" in prompt
        return super().generate_json(
            prompt,
            schema=schema,
            schema_name=schema_name,
            system=system,
        )


class FamilyContentIntentJudge(CompleteIntentJudge):
    def generate_json(
        self,
        prompt: str,
        *,
        schema: object,
        schema_name: str,
        system: str | None = None,
    ) -> str:
        response = json.loads(
            super().generate_json(
                prompt,
                schema=schema,
                schema_name=schema_name,
                system=system,
            )
        )
        response["proposals"]["candidate_2"]["strategy_id"] = "family_game_nights"
        return json.dumps(response)


class NaturalSemanticVocabularyIntentJudge(CompleteIntentJudge):
    def generate_json(
        self,
        prompt: str,
        *,
        schema: object,
        schema_name: str,
        system: str | None = None,
    ) -> str:
        response = json.loads(
            super().generate_json(
                prompt,
                schema=schema,
                schema_name=schema_name,
                system=system,
            )
        )
        for slot, strategy_id in zip(
            response["proposals"],
            ("inspect_board", "test_board", "collaborate_on_case"),
            strict=True,
        ):
            response["proposals"][slot]["strategy_id"] = strategy_id
        return json.dumps(response)


class FirstCaseQuarantinedIntentJudge(CompleteIntentJudge):
    def generate_json(
        self,
        prompt: str,
        *,
        schema: object,
        schema_name: str,
        system: str | None = None,
    ) -> str:
        response = json.loads(
            super().generate_json(
                prompt,
                schema=schema,
                schema_name=schema_name,
                system=system,
            )
        )
        if self.calls <= 3:
            response["proposals"]["candidate_1"]["strategy_id"] = (
                "gold_specific_strategy_v1"
            )
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
    slots = schema["properties"]["proposals"]["properties"]
    branches = [
        branch
        for slot_schema in slots.values()
        for branch in slot_schema["oneOf"]
    ]
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

    assert {branch["properties"]["effect"]["const"] for branch in directed} == {
        "abstain",
        "annotate_conflict",
        "demote",
        "replace",
        "suppress",
        "verify",
    }
    assert {branch["properties"]["effect"]["const"] for branch in unknown} == {
        "abstain",
        "annotate_conflict",
        "verify",
    }
    assert all(
        branch["properties"]["target_item_id"] == {"const": None}
        and branch["properties"]["replacement_item_id"] == {"const": None}
        for branch in unknown
    )
    replace = next(
        branch
        for branch in directed
        if branch["properties"]["effect"] == {"const": "replace"}
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

    assert proposals["required"] == ["candidate_1", "candidate_2", "candidate_3"]
    assert "uniqueItems" not in json.dumps(schema)


def test_intent_schema_partitions_actions_across_closed_candidate_slots() -> None:
    surface = {
        "proposals_needed": 3,
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
    proposals = schema["properties"]["proposals"]
    slots = proposals["properties"]

    assert proposals["additionalProperties"] is False
    assert proposals["required"] == ["candidate_1", "candidate_2", "candidate_3"]
    assert set(slots) == set(proposals["required"])
    action_sets = []
    for slot in proposals["required"]:
        actions = {
            (
                branch["properties"]["relation_edge_id"]["const"],
                branch["properties"]["effect"]["const"],
                branch["properties"]["target_item_id"]["const"],
                branch["properties"]["replacement_item_id"]["const"],
            )
            for branch in slots[slot]["oneOf"]
        }
        assert actions
        action_sets.append(actions)
    assert len(set.union(*action_sets)) == 9
    assert all(
        left.isdisjoint(right)
        for index, left in enumerate(action_sets)
        for right in action_sets[index + 1 :]
    )


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


def test_slotted_structured_intents_produce_gpu_loadable_prepared_cases(
    tmp_path: Path,
) -> None:
    dataset = _dataset(tmp_path)
    output = tmp_path / "prepared_cases.jsonl"
    artifacts = tmp_path / "preparation"
    proposer = SlottedIntentJudge()

    manifest = prepare_live_cases(
        dataset_dir=dataset,
        output_path=output,
        artifacts_dir=artifacts,
        cache_path=tmp_path / "relation_cache.sqlite",
        relation_judge=PositiveRelationJudge(),
        intent_judge=proposer,
        instrument_model_id="relation-model-v1",
        instrument_model_hash="a" * 64,
        proposer_model_id="intent-model-v5",
        proposer_model_hash="b" * 64,
        candidate_budget=4,
        limit=2,
    )

    report = validate_prepared_cases(
        dataset_dir=dataset,
        prepared_path=output,
        manifest_path=artifacts / "preparation_manifest.json",
    )
    assert manifest["build_status"] == "gpu_input_ready"
    assert manifest["proposer_model_call_count"] == proposer.calls == 2
    assert report["decision"] == "PASS", report["reasons"]


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


def test_compiler_rejection_reason_drives_audited_correction_retry(
    tmp_path: Path,
) -> None:
    dataset = _dataset(tmp_path)
    artifacts = tmp_path / "artifacts"
    proposer = CompilerLeakUntilCorrectedIntentJudge()

    manifest = prepare_live_cases(
        dataset_dir=dataset,
        output_path=tmp_path / "prepared.jsonl",
        artifacts_dir=artifacts,
        cache_path=tmp_path / "cache.sqlite",
        relation_judge=PositiveRelationJudge(),
        intent_judge=proposer,
        instrument_model_id="relation-model-v2",
        instrument_model_hash="a" * 64,
        proposer_model_id="intent-model-v5",
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
    assert report["reason_counts"] == {"accepted": 2, "compiler_rejected": 2}
    assert report["model_call_count"] == proposer.calls == 4


def test_runtime_family_content_is_not_mistaken_for_hidden_family_leakage(
    tmp_path: Path,
) -> None:
    dataset = _dataset(tmp_path)
    artifacts = tmp_path / "artifacts"
    proposer = FamilyContentIntentJudge()

    manifest = prepare_live_cases(
        dataset_dir=dataset,
        output_path=tmp_path / "prepared.jsonl",
        artifacts_dir=artifacts,
        cache_path=tmp_path / "cache.sqlite",
        relation_judge=PositiveRelationJudge(),
        intent_judge=proposer,
        instrument_model_id="relation-model-v2",
        instrument_model_hash="a" * 64,
        proposer_model_id="intent-model-v5",
        proposer_model_hash="b" * 64,
        candidate_budget=4,
        max_proposer_retries=2,
        limit=2,
    )

    assert manifest["build_status"] == "gpu_input_ready"
    assert manifest["proposer_reason_counts"] == {"accepted": 2}


def test_runtime_test_and_case_vocabulary_is_not_mistaken_for_id_leakage(
    tmp_path: Path,
) -> None:
    dataset = _dataset(tmp_path)

    manifest = prepare_live_cases(
        dataset_dir=dataset,
        output_path=tmp_path / "prepared.jsonl",
        artifacts_dir=tmp_path / "artifacts",
        cache_path=tmp_path / "cache.sqlite",
        relation_judge=PositiveRelationJudge(),
        intent_judge=NaturalSemanticVocabularyIntentJudge(),
        instrument_model_id="relation-model-v2",
        instrument_model_hash="a" * 64,
        proposer_model_id="intent-model-v6",
        proposer_model_hash="b" * 64,
        candidate_budget=4,
        max_proposer_retries=0,
        limit=2,
    )

    assert manifest["build_status"] == "gpu_input_ready"
    assert manifest["proposer_reason_counts"] == {"accepted": 2}


def test_collect_all_mode_quarantines_one_case_and_prepares_later_cases(
    tmp_path: Path,
) -> None:
    dataset = _dataset(tmp_path)
    artifacts = tmp_path / "artifacts"
    output = tmp_path / "prepared.jsonl"
    progress = tmp_path / "progress.jsonl"
    proposer = FirstCaseQuarantinedIntentJudge()

    result = prepare_live_cases(
        dataset_dir=dataset,
        output_path=output,
        artifacts_dir=artifacts,
        cache_path=tmp_path / "cache.sqlite",
        relation_judge=PositiveRelationJudge(),
        intent_judge=proposer,
        instrument_model_id="relation-model-v2",
        instrument_model_hash="a" * 64,
        proposer_model_id="intent-model-v5",
        proposer_model_hash="b" * 64,
        candidate_budget=4,
        max_proposer_retries=2,
        limit=2,
        progress_path=progress,
        collect_proposer_failures=True,
    )

    assert result["build_status"] == "repair_required"
    assert result["selected_case_count"] == 2
    assert result["prepared_case_count"] == 1
    assert result["quarantined_case_count"] == 1
    assert proposer.calls == 4
    assert not output.exists()
    assert not (artifacts / "preparation_manifest.json").exists()
    assert len(_rows(artifacts / "prepared_cases.partial.jsonl")) == 1
    quarantine = _rows(artifacts / "intent_quarantine.jsonl")
    assert len(quarantine) == 1
    assert quarantine[0]["reason_code"] == "compiler_rejected"
    assert quarantine[0]["quarantine_row_sha256"]
    report = json.loads(
        (artifacts / "intent_proposal_report.json").read_text(encoding="utf-8")
    )
    assert report["decision"] == "REFUSE"
    events = [row["event"] for row in _rows(progress)]
    assert "intent_case_quarantined" in events
    assert events.index("intent_case_quarantined") < events.index("case_prepared")
    assert events[-1] == "preparation_repair_required"


def test_v6_migrates_valid_v5_cache_and_requeries_only_rejected_response(
    tmp_path: Path,
) -> None:
    dataset = _dataset(tmp_path)
    cache = tmp_path / "cache.sqlite"
    prepare_live_cases(
        dataset_dir=dataset,
        output_path=tmp_path / "initial.jsonl",
        artifacts_dir=tmp_path / "initial-artifacts",
        cache_path=cache,
        relation_judge=PositiveRelationJudge(),
        intent_judge=CompleteIntentJudge(),
        instrument_model_id="relation-model-v2",
        instrument_model_hash="a" * 64,
        proposer_model_id="intent-model-v5",
        proposer_model_hash="c" * 64,
        candidate_budget=4,
        limit=2,
    )
    case_by_current_key = {
        row["proposer_cache_key"]: row["case_id"]
        for row in _rows(tmp_path / "initial-artifacts" / "intent_proposals.jsonl")
    }
    legacy_version = "cmd-v4-llm-intent-proposer-v5-disjoint-slots"
    with sqlite3.connect(cache) as connection:
        rows = connection.execute(
            "SELECT cache_key, proposer_input_sha256, prompt_template_sha256, "
            "proposer_model_sha256, proposals_needed, response_json "
            "FROM v4_intent_proposals ORDER BY cache_key"
        ).fetchall()
        connection.execute("DELETE FROM v4_intent_proposals")
        for index, row in enumerate(rows):
            (
                current_key,
                input_sha256,
                prompt_sha256,
                model_sha256,
                proposals_needed,
                response_json,
            ) = row
            response = json.loads(response_json)
            if index == 0:
                response["proposals"]["candidate_1"]["strategy_id"] = (
                    f"semantic_{case_by_current_key[current_key]}"
                )
            legacy_key = canonical_sha256(
                {
                    "proposer_input_sha256": input_sha256,
                    "prompt_template_sha256": prompt_sha256,
                    "proposer_version": legacy_version,
                    "proposer_model_sha256": model_sha256,
                    "proposals_needed": proposals_needed,
                }
            )
            connection.execute(
                "INSERT INTO v4_intent_proposals VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    legacy_key,
                    input_sha256,
                    prompt_sha256,
                    legacy_version,
                    model_sha256,
                    proposals_needed,
                    json.dumps(response, sort_keys=True, separators=(",", ":")),
                    canonical_sha256(response),
                ),
            )

    proposer = LegacyCacheCorrectionIntentJudge()
    result = prepare_live_cases(
        dataset_dir=dataset,
        output_path=tmp_path / "replay.jsonl",
        artifacts_dir=tmp_path / "replay-artifacts",
        cache_path=cache,
        relation_judge=PositiveRelationJudge(),
        intent_judge=proposer,
        instrument_model_id="relation-model-v2",
        instrument_model_hash="a" * 64,
        proposer_model_id="intent-model-v6",
        proposer_model_hash="b" * 64,
        legacy_proposer_model_hashes={legacy_version: "c" * 64},
        candidate_budget=4,
        limit=2,
        collect_proposer_failures=True,
    )

    assert result["build_status"] == "gpu_input_ready"
    assert result["proposer_version"] == (
        "cmd-v4-llm-intent-proposer-v6-semantic-id-validation"
    )
    assert result["proposer_model_call_count"] == proposer.calls == 1
    assert result["proposer_cache_hit_count"] == 1
    responses = _rows(
        tmp_path / "replay-artifacts" / "intent_responses.jsonl"
    )
    assert sorted(row["reason_code"] for row in responses) == [
        "accepted",
        "cache_rejected",
        "cache_replay",
    ]
    with sqlite3.connect(cache) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM v4_intent_proposals WHERE proposer_version = ?",
            (result["proposer_version"],),
        ).fetchone()[0] == 2
    validation = validate_prepared_cases(
        dataset_dir=dataset,
        prepared_path=tmp_path / "replay.jsonl",
        manifest_path=tmp_path / "replay-artifacts" / "preparation_manifest.json",
    )
    assert validation["decision"] == "PASS"


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
    ledger[0]["proposer_response"]["proposals"]["candidate_1"]["strategy_id"] = (
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
