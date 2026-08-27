from __future__ import annotations

import json
from typing import Mapping

import pytest

from cmd_audit.repair.ghost_ecology import SkillRevision
from cmd_audit.spec_v03.backbone_provider import (
    BackboneProviderConfig,
    BackboneProviderError,
    DeterministicDevelopmentProvider,
    OpenAICompatibleBackboneProvider,
    ProviderBudget,
    build_backbone_prompt,
)
from cmd_audit.spec_v03.contracts import DecisionView
from cmd_audit.spec_v03.repair_stream import MemoryState


def _decision() -> DecisionView:
    return DecisionView(
        case_id="case-provider", source_dataset_id="public", source_episode_id="episode-1",
        family_id="family-1", lineage_id="lineage-1", event_index=3,
        observation={"event_log": [{"event_id": "event-1", "content": "visible fact"}]},
        provenance={"source_sha256": "a" * 64}, unsupported_fields=("sealed_fields_omitted",),
    )


def _state() -> MemoryState:
    return MemoryState((), (), (), (), (), (), (), ())


def _skill(name: str) -> SkillRevision:
    return SkillRevision.create(
        skill_id=f"runtime:{name}", program={"kind": "typed_operator", "operator_id": name},
        parameter_schema={"type": "object", "additionalProperties": False},
        preconditions=({"kind": "always"},), postconditions=(), success_probe={"probe_id": f"probe:{name}"},
        mutation_budget={"locality": 1}, rollback_program={"action": "rollback"},
        producing_failure_id="must-not-appear-in-prompt", derivation_kind="seed", state="stable",
    )


def _development_provider(*, budget: ProviderBudget | None = None) -> DeterministicDevelopmentProvider:
    return DeterministicDevelopmentProvider(
        BackboneProviderConfig("development/hash-v1", "dev-snapshot-1", "DEVELOPMENT", max_output_tokens=8),
        budget or ProviderBudget(5, 10_000),
    )


def test_deterministic_provider_is_reproducible_audited_and_explicitly_non_model() -> None:
    decision, state, skills = _decision(), _state(), (_skill("one"), _skill("two"))
    first = _development_provider().predict(decision, state, skills)
    second_provider = _development_provider()
    second = second_provider.predict(decision, state, skills)

    assert first == second
    audit = second_provider.call_audit[0]
    assert audit.provider_kind == "deterministic_development_non_model"
    assert audit.snapshot == "dev-snapshot-1"
    assert audit.prediction_sha256 == second.prediction_sha256
    assert second_provider.usage.request_count == 1
    with pytest.raises(ValueError, match="DEVELOPMENT-only"):
        DeterministicDevelopmentProvider(
            BackboneProviderConfig("x", "snapshot", "PRODUCTION", endpoint="https://example.test"),
            ProviderBudget(1, 100),
        )
    with pytest.raises(ValueError, match="model_id"):
        BackboneProviderConfig("", "snapshot", "DEVELOPMENT")
    with pytest.raises(ValueError, match="snapshot"):
        BackboneProviderConfig("model", "", "DEVELOPMENT")


def test_prompt_contains_only_typed_candidate_content_not_skill_provenance() -> None:
    prompt = build_backbone_prompt(decision=_decision(), state=_state(), candidates=(_skill("one"),))
    rendered = json.dumps(prompt, sort_keys=True)
    assert "must-not-appear-in-prompt" not in rendered
    assert "producing_failure_id" not in rendered
    assert prompt["candidate_operators"][0]["program"] == {"kind": "typed_operator", "operator_id": "one"}  # type: ignore[index]


def test_budget_is_preflighted_without_creating_a_call_record() -> None:
    provider = _development_provider(budget=ProviderBudget(0, 10_000))
    with pytest.raises(BackboneProviderError, match="request budget"):
        provider.predict(_decision(), _state(), (_skill("one"),))
    assert provider.call_audit == ()
    assert provider.usage.request_count == 0


class _FakeTransport:
    def __init__(self, response: Mapping[str, object]) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def post_json(self, *, url: str, headers: Mapping[str, str], body: Mapping[str, object], timeout_seconds: float) -> Mapping[str, object]:
        self.calls.append({"url": url, "headers": dict(headers), "body": dict(body), "timeout_seconds": timeout_seconds})
        return self.response


def _production_provider(response: Mapping[str, object]) -> tuple[OpenAICompatibleBackboneProvider, _FakeTransport]:
    transport = _FakeTransport(response)
    provider = OpenAICompatibleBackboneProvider(
        BackboneProviderConfig("qwen3-14b", "qwen3-14b-2026-08-01", "PRODUCTION", endpoint="https://provider.test/v1", api_key="secret", max_output_tokens=16),
        ProviderBudget(2, 1_000), transport=transport,
    )
    return provider, transport


def _response(skills: tuple[SkillRevision, ...], output: object, *, model: str = "qwen3-14b", usage: Mapping[str, object] | None = None) -> dict[str, object]:
    del skills
    return {
        "model": model,
        "system_fingerprint": "qwen3-14b-2026-08-01",
        "choices": [{"message": {"role": "assistant", "content": json.dumps(output)}}],
        "usage": dict(usage or {"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15}),
    }


def test_openai_compatible_provider_uses_injected_transport_and_closed_output() -> None:
    decision, state, skills = _decision(), _state(), (_skill("one"), _skill("two"))
    ids = tuple(skill.skill_revision_id for skill in skills)
    provider, transport = _production_provider(_response(skills, {
        "selected_skill_revision_id": ids[1], "scores": {ids[0]: -0.25, ids[1]: 0.75},
    }))

    prediction = provider.predict(decision, state, skills)

    assert prediction.model_id == "qwen3-14b"
    assert prediction.selected_skill_revision_id == ids[1]
    assert provider.usage.total_tokens == 15
    assert transport.calls[0]["url"] == "https://provider.test/v1/chat/completions"
    prompt = transport.calls[0]["body"]["messages"][1]["content"]  # type: ignore[index]
    assert "must-not-appear-in-prompt" not in prompt
    assert provider.call_audit[0].snapshot == "qwen3-14b-2026-08-01"


@pytest.mark.parametrize("output", [
    {"selected_skill_revision_id": "wrong", "scores": {}},
    {"selected_skill_revision_id": "x", "scores": {}, "extra": 1},
    {"selected_skill_revision_id": "x", "scores": {"x": float("nan")}},
])
def test_openai_provider_rejects_missing_extra_and_nan_candidate_output(output: object) -> None:
    skills = (_skill("one"),)
    # JSON permits NaN in Python's encoder; the receiving provider must still reject it.
    provider, _transport = _production_provider(_response(skills, output))
    with pytest.raises(BackboneProviderError):
        provider.predict(_decision(), _state(), skills)
    assert provider.call_audit == ()


def test_openai_provider_rejects_model_or_usage_contract_mismatch() -> None:
    skills = (_skill("one"),)
    skill_id = skills[0].skill_revision_id
    provider, _transport = _production_provider(_response(
        skills, {"selected_skill_revision_id": skill_id, "scores": {skill_id: 0.2}}, model="other",
    ))
    with pytest.raises(BackboneProviderError, match="model_id"):
        provider.predict(_decision(), _state(), skills)

    provider, _transport = _production_provider(_response(
        skills, {"selected_skill_revision_id": skill_id, "scores": {skill_id: 0.2}},
    ))
    provider._transport.response = {**provider._transport.response, "system_fingerprint": "other-snapshot"}  # type: ignore[attr-defined]
    with pytest.raises(BackboneProviderError, match="snapshot"):
        provider.predict(_decision(), _state(), skills)

    provider, _transport = _production_provider(_response(
        skills, {"selected_skill_revision_id": skill_id, "scores": {skill_id: 0.2}},
        usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 9},
    ))
    with pytest.raises(ValueError, match="total_tokens"):
        provider.predict(_decision(), _state(), skills)


def test_openai_provider_accepts_explicit_chat_completion_path_and_cumulative_preflight() -> None:
    skills = (_skill("one"),)
    skill_id = skills[0].skill_revision_id
    transport = _FakeTransport(_response(skills, {"selected_skill_revision_id": skill_id, "scores": {skill_id: 0.2}}))
    provider = OpenAICompatibleBackboneProvider(
        BackboneProviderConfig(
            "qwen3-14b", "qwen3-14b-2026-08-01", "PRODUCTION",
            endpoint="https://provider.test/v1/chat/completions", api_key="secret", max_output_tokens=16,
        ),
        ProviderBudget(1, 1_000), transport=transport,
    )
    provider.predict(_decision(), _state(), skills)
    assert transport.calls[0]["url"] == "https://provider.test/v1/chat/completions"
    with pytest.raises(BackboneProviderError, match="request budget"):
        provider.predict(_decision(), _state(), skills)
    assert provider.usage.request_count == 1

    provider, _transport = _production_provider(_response(
        skills, {"selected_skill_revision_id": skill_id, "scores": {skill_id: 0.2}},
        usage={"prompt_tokens": 3, "completion_tokens": 17, "total_tokens": 20},
    ))
    with pytest.raises(BackboneProviderError, match="max_output_tokens"):
        provider.predict(_decision(), _state(), skills)
