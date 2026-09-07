from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from experiments.ghost_ecology_zero_call import (
    FEEDBACK_V2_SCHEMA_VERSION,
    FEEDBACK_SCHEMA_VERSION,
    REPORT_V2_SCHEMA_VERSION,
    REGISTERED_PROBES,
    audit_identifiability_v2,
    content_sha256,
    deployment_feedback,
    deployment_feedback_v2,
    deployment_reward_v2,
    deployment_reward,
    _TypedObservation,
    _typed_statistics,
    v2_protocol_manifest,
)


@dataclass
class _Telemetry:
    valid: bool = True
    rolled_back: bool = False
    changed_item_count: int = 0
    locality_cost: float = 0.0

    @property
    def recovery_gain(self) -> float:
        raise AssertionError("deployment feedback read shadow recovery")


def test_skill_conditioned_probes_distinguish_noop_and_mutating_skills() -> None:
    verify = deployment_feedback("verify", _Telemetry(changed_item_count=0))
    replace = deployment_feedback("replace", _Telemetry(changed_item_count=1))
    wrong_verify = deployment_feedback("verify", _Telemetry(changed_item_count=1))

    assert verify["schema_version"] == FEEDBACK_SCHEMA_VERSION
    assert verify["probe_id"] == REGISTERED_PROBES["verify"]
    assert verify["success"] == 1.0
    assert replace["success"] == 1.0
    assert wrong_verify["success"] == 0.0
    assert all(row["gold_derived"] is False for row in (verify, replace))


def test_feedback_is_gold_free_and_penalizes_guard_failure() -> None:
    assert deployment_reward("abstain", _Telemetry()) == 1.0
    assert deployment_reward("demote", _Telemetry(changed_item_count=1)) == 0.95
    assert deployment_reward(
        "suppress", _Telemetry(valid=False, changed_item_count=1)
    ) == -1.0
    with pytest.raises(ValueError, match="unregistered"):
        deployment_feedback("invented", _Telemetry())


def test_typed_feedback_consumes_observed_actionability_mode() -> None:
    telemetry = _Telemetry(changed_item_count=1)
    telemetry.actionability_mode = "legacy-wrong"
    telemetry.actionability_mode_observed = "destructive"
    telemetry.target_binding_observed = True
    telemetry.target_match_observed = True

    feedback = deployment_feedback_v2(
        "replace", telemetry, expected_actionability_mode="destructive"
    )
    assert feedback["success"] is True
    assert feedback["coverage"] is True
    assert "actionability_mode_observed" in feedback["observed_fields"]
    assert deployment_reward_v2(
        "replace", telemetry, expected_actionability_mode="destructive"
    ) is not None


def test_typed_v2_schema_manifest_and_coverage_block_are_explicit(monkeypatch, tmp_path) -> None:
    @dataclass(frozen=True)
    class LegacyOutcome:
        intent_id: str
        recovery_gain: float = 0.2
        locality_cost: float = 0.0
        changed_item_count: int = 1
        valid: bool = True
        rolled_back: bool = False

    cases = tuple(SimpleNamespace(
        case_id=f"c{i}", family_id=f"f{i}", semantic_cluster="dev-a",
        intents=(SimpleNamespace(intent_id="i", effect="replace"),),
        candidate_outcomes=(LegacyOutcome("i"),),
    ) for i in range(2))
    monkeypatch.setattr("experiments.ghost_ecology_zero_call.load_cases", lambda _: cases)
    source = tmp_path / "cases.jsonl"; source.write_text("legacy")
    report = audit_identifiability_v2(cases_path=source, output=tmp_path / "report.json", bootstrap_samples=100)
    manifest = v2_protocol_manifest()
    assert report["schema_version"] == REPORT_V2_SCHEMA_VERSION
    assert report["feedback_schema_version"] == FEEDBACK_V2_SCHEMA_VERSION
    assert report["protocol_manifest"]["schema_version"] == REPORT_V2_SCHEMA_VERSION
    claimed = dict(report["protocol_manifest"]); digest = claimed.pop("manifest_sha256")
    assert digest == content_sha256(claimed)
    assert report["decision"] == "BLOCKED_TYPED_EVIDENCE_UNAVAILABLE"
    assert report["decoupling_controls"]["telemetry_permutation"]["status"] == "NOT_RUN_COVERAGE_BLOCKED"
    for key in ("candidate_level_pearson", "family_macro_pearson", "family_bootstrap_lower_95_one_sided", "within_case_pairwise_concordance", "comparable_pair_count"):
        assert report["typed_coverage"][key] is None


def test_typed_statistics_fixture_has_two_identity_bound_candidate_pairs() -> None:
    @dataclass(frozen=True)
    class TypedOutcome:
        valid: bool = True
        rolled_back: bool = False
        changed_item_count: int = 1
        locality_cost: float = 0.0
        actionability_mode_observed: str = "destructive"
        target_binding_observed: bool = True
        target_match_observed: bool = True
        recovery_gain: float = 0.0

    left = TypedOutcome(recovery_gain=0.9)
    right = TypedOutcome(locality_cost=0.1, recovery_gain=0.1)
    stats = _typed_statistics(
        (
            _TypedObservation("c", "f", "replace", left, left, "destructive"),
            _TypedObservation("c", "f", "demote", right, right, "destructive"),
        ),
        bootstrap_samples=100,
    )
    assert stats["candidate_observed_coverage"] == 1.0
    assert stats["pairwise_comparable_coverage"]["total_nonreference_tied_candidate_pairs"] == 1
    assert stats["pairwise_comparable_coverage"]["comparable"] == 1
