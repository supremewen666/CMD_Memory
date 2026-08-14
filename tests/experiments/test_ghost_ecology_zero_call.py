from __future__ import annotations

from dataclasses import dataclass

import pytest

from experiments.ghost_ecology_zero_call import (
    FEEDBACK_SCHEMA_VERSION,
    REGISTERED_PROBES,
    deployment_feedback,
    deployment_reward,
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
