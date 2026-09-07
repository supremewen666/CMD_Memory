from __future__ import annotations

import pytest

from cmd_audit.core.state_machine import (
    ControlRegisters, StateTransition, TransitionJournal, default_router_registry,
)


def _registers() -> ControlRegisters:
    return ControlRegisters(0, -1, "global_policy", "router-root", "registry-root", {"failure": "m-root"}, {"repo": "r-root"}, "manifest-root")


def test_router_registry_is_pure_and_closed() -> None:
    registry = default_router_registry()
    assert registry.ids == ("ghost_hierarchy", "global_policy", "observable_residual_ghost")
    result = registry.get("global_policy").apply(_registers(), {"selection": "global_policy"})
    assert result.registers.root == _registers().root
    with pytest.raises(ValueError, match="closed"):
        registry.get("global_policy").apply(_registers(), {"selection": "x", "memory": "bad"})


def test_journal_replay_and_tamper_detection(tmp_path) -> None:
    journal = TransitionJournal(tmp_path / "transitions.jsonl")
    prepared = StateTransition("global_policy", "policy_update", "prepared", 0, "before", "after", "operand")
    journal.append(prepared)
    committed = StateTransition("global_policy", "policy_update", "committed", 1, "before", "after", "operand", prepared.transition_hash)
    journal.append(committed)
    assert len(TransitionJournal(journal.path).replay()) == 2
    journal.path.write_text(journal.path.read_text().replace('"after_root":"after"', '"after_root":"tampered"', 1), encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        TransitionJournal(journal.path)


def test_transition_rejects_incorrect_incident_family() -> None:
    with pytest.raises(ValueError, match="one-to-one"):
        StateTransition("incident_triage", "incident", "committed", 0, "a", "b", "c", incident_mechanism="state_drift", repair_family="pipeline_patch")
