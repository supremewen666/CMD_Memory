from __future__ import annotations

import pytest

from cmd_audit.repair.evolution_repository import EvolutionRepository, content_sha256


def _selection() -> dict[str, object]:
    return {
        "case_id": "case-later", "event_index": 3, "graph_sha256": "graph",
        "ranked_intent_ids": ["intent-a"], "selected_intent_id": "intent-a",
        "reason": "selected", "policy_snapshot_sha256": "policy",
    }


def test_append_replay_and_reopen_are_content_bound(tmp_path) -> None:
    path = tmp_path / "evolution.sqlite"
    with EvolutionRepository(path) as repository:
        selection_id = repository.append_selection(_selection())
        assert repository.append_selection(_selection()) == selection_id
        repository.append_outcome({
            "selection_id": selection_id, "case_id": "case-later",
            "observed_after_event_index": 4, "intent_id": "intent-a",
            "recovery_gain": 1.0, "locality_cost": 0.0, "changed_item_count": 1,
            "valid": True, "rolled_back": False,
        })
        expected = repository.repository_hash()
    with EvolutionRepository(path) as reopened:
        assert reopened.repository_hash() == expected
        assert len(reopened.rows("outcome")) == 1


def test_outcome_cannot_precede_selection_or_conflict_on_replay() -> None:
    repository = EvolutionRepository()
    selection_id = repository.append_selection(_selection())
    with pytest.raises(ValueError, match="after"):
        repository.append_outcome({
            "selection_id": selection_id, "case_id": "case-later",
            "observed_after_event_index": 3,
        })
    with pytest.raises(ValueError, match="canonical payload"):
        repository.append_selection({**_selection(), "selection_id": "forged"})


def test_snapshot_and_active_views_are_append_only() -> None:
    repository = EvolutionRepository()
    snapshot = {"effective_after_event_index": -1, "weights": {"a": 1.0}}
    snapshot_hash = repository.append_policy_snapshot(snapshot)
    assert snapshot_hash == content_sha256(snapshot)
    child = {
        "effective_after_event_index": 4,
        "parent_snapshot_sha256": snapshot_hash,
        "weights": {"a": 2.0},
    }
    assert repository.append_policy_snapshot(child) == content_sha256(child)
    species_id = repository.append_species({"strategy_id": "trusted-later", "effect": "demote"})
    repository.append_niche_membership({"species_id": species_id, "niche_path": "global"})
    assert [row["species_id"] for row in repository.active_species()] == [species_id]
    repository.append_lifecycle_event({"subject_id": species_id, "to_state": "retired", "event_index": 9})
    assert repository.active_species() == ()
    assert len(repository.rows("lifecycle")) == 1


def test_chain_rows_use_reusable_strategy_species_ids() -> None:
    repository = EvolutionRepository()
    attempt_id = repository.append_chain_attempt({
        "first_strategy_id": "prefer-trusted-later@v1",
        "second_strategy_id": "verify-after-repair@v1",
        "case_id": "later-case",
        "event_index": 8,
    })
    decision_id = repository.append_chain_decision({
        "chain_id": "chain:stable-order",
        "to_state": "probation",
        "event_index": 8,
        "attempt_id": attempt_id,
    })

    assert repository.rows("chain_attempt")[0]["event_id"] == attempt_id
    assert repository.rows("chain_decision")[0]["event_id"] == decision_id
    assert repository.active_chains()[0]["chain_id"] == "chain:stable-order"
    repository.append_chain_decision({
        "chain_id": "chain:stable-order",
        "to_state": "retired",
        "event_index": 9,
        "reason": "anchor_regression",
    })
    assert repository.active_chains() == ()
