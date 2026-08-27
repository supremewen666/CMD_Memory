from __future__ import annotations

import json
from typing import Mapping

import pytest

from cmd_audit.repair.ghost_ecology import FailureDeposit
from cmd_audit.spec_v03.contracts import canonical_sha256
from cmd_audit.spec_v03.repair_stream import PublicEpisode, PublicEvent, PublicQuery, build_intervention, compile_repair_case
from cmd_audit.spec_v03.runtime_bundle import deserialize, serialize
from cmd_audit.spec_v03.skill_discovery_provider import (
    DiscoveryBudget, OpenAICompatibleSkillDiscoveryProvider, SkillDiscoveryConfig,
    SkillDiscoveryProviderError, _bounded_event, _bounded_ids, load_skill_library_mapping, serialize_skill_library,
)


def _bundle():
    def event(event_id: str, ordinal: int, payload: dict[str, object]) -> PublicEvent:
        digest = canonical_sha256(payload)
        return PublicEvent(event_id, f"fixture:{event_id}", ordinal, None, "user", payload, digest, digest)

    episode = PublicEpisode(
        "fixture:episode", "fixture", "fixture-family", "fixture.json", "a" * 64,
        (event("event-alpha", 0, {"memory": "alpha"}), event("event-beta", 1, {"memory": "beta"})),
        (PublicQuery("fixture:q", "What is current?", "beta", (), "fixture:q"),),
        ("process", "state", "poison"), {"fixture": "skill-discovery"},
    )
    clean = compile_repair_case(episode, build_intervention(episode, "drop", seed=3))
    return deserialize(serialize(
        case_id=clean.case_id, source_dataset_id=clean.decision_view.source_dataset_id,
        source_episode_id=clean.decision_view.source_episode_id, family_id=clean.decision_view.family_id,
        lineage_id=clean.decision_view.lineage_id, source_event_ids=tuple(event.event_id for event in clean.corrupt_state.immutable_source_log),
        decision_view=clean.decision_view, memory_state=clean.corrupt_state,
    ))


def _failure(bundle):
    return FailureDeposit("failure-a", bundle.case_id, bundle.family_id, "a" * 64, (("event_count", 1.0),), "b" * 64, "c" * 64)


class _FakeTransport:
    def __init__(self, response: Mapping[str, object]) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def post_json(self, *, url: str, headers: Mapping[str, str], body: Mapping[str, object], timeout_seconds: float) -> Mapping[str, object]:
        self.calls.append({"url": url, "headers": dict(headers), "body": dict(body)})
        return self.response


def _response(content: object, *, model: str = "Qwen2.5-14B-Instruct") -> dict[str, object]:
    return {
        "model": model,
        "choices": [{"message": {"role": "assistant", "content": json.dumps(content)}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
    }


def _provider(response: Mapping[str, object]):
    transport = _FakeTransport(response)
    provider = OpenAICompatibleSkillDiscoveryProvider(
        SkillDiscoveryConfig("Qwen2.5-14B-Instruct", "sha256:model-manifest", "http://127.0.0.1:8000/v1", max_output_tokens=32),
        DiscoveryBudget(2, 100_000), transport=transport,
    )
    return provider, transport


def test_vllm_provider_compiles_catalog_owned_typed_revisions_and_caches() -> None:
    bundle, failure = _bundle(), None
    failure = _failure(bundle)
    provider, transport = _provider(_response({"candidates": [
        {"operator_id": "process_restore", "skill_key": "repair-drop"},
    ]}))

    first = provider.candidates(bundle, event_index=4, failure=failure)
    second = provider.candidates(bundle, event_index=4, failure=failure)

    assert first == second
    assert len(transport.calls) == 1
    assert provider.usage.request_count == 1
    assert provider.discovered_skills == first
    assert first[0].program["kind"] == "cmd-spec-v03-operator"
    assert first[0].program["write_contract"] == "projection"
    assert first[0].producing_failure_id == failure.failure_id
    prompt = transport.calls[0]["body"]["messages"][1]["content"]  # type: ignore[index]
    assert "evaluator" not in prompt.casefold()
    assert provider.call_audit[0].snapshot_binding == "external_manifest"


def test_prompt_bounds_large_event_content_with_auditable_preview() -> None:
    event = _bounded_event({"event_id": "large", "content": {"memory": "x" * 100_000}})

    assert event["content_truncated"] is True
    assert len(event["content_preview"]) == 1024
    assert event["content_chars"] > 100_000
    assert len(event["content_sha256"]) == 64

    ids = _bounded_ids(tuple(f"event-{index}" for index in range(100)))
    assert ids["count"] == 100
    assert len(ids["ids"]) == 64
    assert ids["truncated"] is True


def test_provider_accepts_vllm_message_extension_fields() -> None:
    bundle = _bundle()
    response = _response({"candidates": [{"operator_id": "process_restore", "skill_key": "x"}]})
    response["choices"][0]["message"]["tool_calls"] = []
    provider, _ = _provider(response)

    assert provider.candidates(bundle, event_index=0, failure=_failure(bundle))


@pytest.mark.parametrize("content", [
    {"candidates": [{"operator_id": "unknown", "skill_key": "bad"}]},
    {"candidates": [{"operator_id": "process_restore", "skill_key": "ok", "extra": 1}]},
    {"candidates": []},
])
def test_provider_rejects_closed_output_and_unknown_operators(content: object) -> None:
    bundle = _bundle()
    provider, _ = _provider(_response(content))
    with pytest.raises(SkillDiscoveryProviderError):
        provider.candidates(bundle, event_index=0, failure=_failure(bundle))


def test_provider_normalizes_non_executable_model_skill_labels() -> None:
    bundle = _bundle()
    provider, _ = _provider(_response({"candidates": [
        {"operator_id": "process_restore", "skill_key": "Restore Projection / V1"},
    ]}))

    skill = provider.candidates(bundle, event_index=0, failure=_failure(bundle))[0]

    assert skill.skill_id == "catalog:process_restore:restore-projection-v1"


def test_non_loopback_requires_key_and_response_fingerprint_is_strict() -> None:
    with pytest.raises(ValueError, match="api_key"):
        SkillDiscoveryConfig("m", "pin", "https://provider.test/v1")
    bundle = _bundle()
    provider = OpenAICompatibleSkillDiscoveryProvider(
        SkillDiscoveryConfig("m", "pin", "http://localhost:8000/v1", snapshot_binding="response_fingerprint"),
        DiscoveryBudget(1, 100_000), transport=_FakeTransport(_response({"candidates": [{"operator_id": "process_restore", "skill_key": "x"}]}, model="m")),
    )
    with pytest.raises(SkillDiscoveryProviderError, match="snapshot"):
        provider.candidates(bundle, event_index=0, failure=_failure(bundle))


def test_closed_skill_library_round_trip_and_rejects_duplicates_and_unknown_fields() -> None:
    bundle = _bundle()
    provider, _ = _provider(_response({"candidates": [{"operator_id": "process_restore", "skill_key": "x"}]}))
    skills = provider.candidates(bundle, event_index=0, failure=_failure(bundle))
    encoded = serialize_skill_library(skills)
    assert load_skill_library_mapping(encoded).skills == skills
    duplicate = dict(encoded)
    duplicate["skills"] = [encoded["skills"][0], encoded["skills"][0]]  # type: ignore[index]
    duplicate["library_sha256"] = canonical_sha256({"schema_version": duplicate["schema_version"], "skills": duplicate["skills"]})
    with pytest.raises(ValueError, match="duplicate"):
        load_skill_library_mapping(duplicate)
    extra = dict(encoded)
    extra["extra"] = True
    with pytest.raises(ValueError, match="closed"):
        load_skill_library_mapping(extra)
