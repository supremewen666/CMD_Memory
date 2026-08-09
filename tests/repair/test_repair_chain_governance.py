from __future__ import annotations

import pytest

from cmd_audit.repair.repair_chain_governance import (
    ChainAttemptInput,
    RepairChainGovernor,
)


def _attempt(
    *,
    case: str,
    family: str,
    event: int,
    first: str = "prefer_trusted_later_then_verify@v1",
    second: str = "suppress_stale_conflict@v1",
    first_utility: float = 0.20,
    second_utility: float = 0.10,
    chain_utility: float = 0.50,
    **changes: object,
) -> ChainAttemptInput:
    values: dict[str, object] = {
        "case_id": case,
        "family_id": family,
        "event_index": event,
        "first_strategy_id": first,
        "second_strategy_id": second,
        "first_utility": first_utility,
        "second_utility": second_utility,
        "chain_utility": chain_utility,
        "materialized_intermediate": True,
        "changed_item_count": 1,
        "locality_cost": 0.01,
        "valid": True,
        "rolled_back": False,
        "typed_conflict": False,
        "anchor_regression": False,
    }
    values.update(changes)
    return ChainAttemptInput(**values)


def test_chain_promotes_only_from_later_cross_family_materialized_evidence():
    governor = RepairChainGovernor(
        min_support=2,
        min_families=2,
        min_conservative_benefit=0.05,
    )
    governor.admit_strategy("prefer_trusted_later_then_verify@v1")
    governor.admit_strategy("suppress_stale_conflict@v1")

    first = governor.record_attempt(_attempt(case="seed", family="f0", event=1))
    assert first.lifecycle == "candidate"
    assert first.reason == "seed_evidence_not_self_validating"

    probation = governor.record_attempt(
        _attempt(case="later-a", family="f1", event=2)
    )
    assert probation.lifecycle == "probation"

    stable = governor.record_attempt(
        _attempt(case="later-b", family="f2", event=3, chain_utility=0.55)
    )
    assert stable.lifecycle == "stable"
    assert stable.chain_benefit == pytest.approx(0.35)
    assert stable.decision_sha256 == stable.recomputed_sha256()
    assert stable.payload["chain_id"].startswith("chain:")


def test_conflict_or_missing_materialized_state_retires_chain_as_anti_pattern():
    governor = RepairChainGovernor()
    governor.admit_strategy("a@v1")
    governor.admit_strategy("b@v1")

    decision = governor.record_attempt(
        _attempt(
            case="bad",
            family="f1",
            event=1,
            first="a@v1",
            second="b@v1",
            materialized_intermediate=False,
        )
    )
    assert decision.lifecycle == "retired"
    assert decision.anti_pattern
    assert decision.reason == "missing_materialized_intermediate"


def test_reverse_direction_dominance_retires_weaker_order():
    governor = RepairChainGovernor(
        min_support=2,
        min_families=2,
        reverse_margin=0.01,
    )
    governor.admit_strategy("a@v1")
    governor.admit_strategy("b@v1")
    governor.record_attempt(
        _attempt(
            case="seed-forward",
            family="f0",
            event=1,
            first="a@v1",
            second="b@v1",
            chain_utility=0.35,
        )
    )
    governor.record_attempt(
        _attempt(
            case="reverse-1",
            family="f1",
            event=2,
            first="b@v1",
            second="a@v1",
            chain_utility=0.80,
        )
    )
    decision = governor.record_attempt(
        _attempt(
            case="reverse-2",
            family="f2",
            event=3,
            first="b@v1",
            second="a@v1",
            chain_utility=0.80,
        )
    )
    assert decision.lifecycle == "probation"
    decision = governor.record_attempt(
        _attempt(
            case="reverse-3",
            family="f3",
            event=4,
            first="b@v1",
            second="a@v1",
            chain_utility=0.80,
        )
    )
    assert decision.lifecycle == "stable"

    forward = governor.evaluate("a@v1", "b@v1")
    assert forward.lifecycle == "retired"
    assert forward.reason == "reverse_direction_dominates"
    assert forward.anti_pattern


def test_chain_rejects_unadmitted_component_and_exposes_canonical_repository_row():
    governor = RepairChainGovernor()
    governor.admit_strategy("a@v1")

    decision = governor.record_attempt(
        _attempt(
            case="bad",
            family="f1",
            event=1,
            first="a@v1",
            second="not-admitted@v1",
        )
    )
    assert decision.lifecycle == "blocked"
    assert decision.reason == "component_not_admitted"
    row = decision.repository_row()
    assert row["payload_sha256"] == decision.decision_sha256
    assert row["record_type"] == "repair_chain_governance_decision"
