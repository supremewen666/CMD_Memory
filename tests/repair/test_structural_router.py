from __future__ import annotations

import random
from types import SimpleNamespace

from cmd_audit.core.models import MemoryItem
from cmd_audit.counterfactual.actions import PipelineAction
from cmd_audit.counterfactual.operators import OperatorSpec
from cmd_audit.repair.scope_ledger import ScopeLedger
from cmd_audit.repair.skill_ecology import SkillCandidate
from cmd_audit.repair.structural_router import (
    COLLISION_SIGNAL,
    COVERAGE_SIGNAL,
    RECENCY_SIGNAL,
    SAFETY_SIGNAL,
    StructuralIndication,
    ScopePolicy,
    extract_structural_indications,
    item_gate_result_to_indications,
    route,
)


def _candidate(skill_id: str, action: PipelineAction) -> SkillCandidate:
    return SkillCandidate(skill_id, OperatorSpec.single(0, action))


def test_injected_safety_item_metadata_is_not_a_signal() -> None:
    indications = extract_structural_indications(
        "evidence",
        (
            MemoryItem(
                memory_id="m0",
                text="evidence",
                passed_safety_filter=True,
            ),
        ),
    )

    assert all(row.signal_type != SAFETY_SIGNAL for row in indications)


def test_safety_requires_an_independent_query_recall_extractor() -> None:
    def independent(_query, items):
        return (
            StructuralIndication(
                SAFETY_SIGNAL,
                "safety_error",
                0.9,
                tuple(item.memory_id for item in items),
                extractor_version="independent-safety-v1",
                model_identity="safety-model",
                prompt_sha256="a" * 64,
            ),
        )

    indications = extract_structural_indications(
        "evidence",
        (MemoryItem("m0", "evidence"),),
        independent_safety_extractor=independent,
    )

    assert indications[0].signal_type == SAFETY_SIGNAL
    assert indications[0].model_identity == "safety-model"


def test_coverage_and_recency_extractors_are_deterministic() -> None:
    items = (
        MemoryItem(
            "old",
            "Alice lives in Paris",
            store="2025-01-01T00:00:00Z",
        ),
        MemoryItem(
            "new",
            "Alice lives in Berlin",
            store="2025-02-01T00:00:00Z",
        ),
    )

    indications = extract_structural_indications(
        "What is the quantum flux capacitor value?",
        items,
    )

    assert {row.signal_type for row in indications} >= {
        COVERAGE_SIGNAL,
        RECENCY_SIGNAL,
    }
    recency = next(row for row in indications if row.signal_type == RECENCY_SIGNAL)
    assert recency.evidence_ids == ("new", "old")


def test_timestamp_gap_without_content_change_does_not_fire() -> None:
    indications = extract_structural_indications(
        "Where does Alice live?",
        (
            MemoryItem(
                "old",
                "Alice lives in Paris",
                store="2025-01-01T00:00:00Z",
            ),
            MemoryItem(
                "new",
                "Alice lives in Paris",
                store="2025-02-01T00:00:00Z",
            ),
        ),
    )

    assert all(row.signal_type != RECENCY_SIGNAL for row in indications)


def test_live_item_gate_result_becomes_provenanced_runtime_signal() -> None:
    target = MemoryItem("old", "Alice lives in Paris")
    newer = MemoryItem("new", "Alice lives in Berlin")
    result = SimpleNamespace(
        status=SimpleNamespace(value="item_stale"),
        target_item=target,
        collision_results=(
            SimpleNamespace(
                item_a=target,
                item_b=newer,
                timestamp_direction="b_newer",
                divergence=SimpleNamespace(max_divergence=0.8),
            ),
        ),
        loo_result=None,
    )

    indications = item_gate_result_to_indications(
        result,
        model_identity="judge-v1",
        prompt_sha256="a" * 64,
    )

    assert len(indications) == 1
    assert indications[0].signal_type == RECENCY_SIGNAL
    assert indications[0].evidence_ids == ("new", "old")
    assert indications[0].strength == 0.8
    assert indications[0].runtime_surface == "tier2_item_gate"
    assert indications[0].model_identity == "judge-v1"


def test_live_item_gate_hitl_and_poisoned_outcomes_abstain() -> None:
    for status in ("hitl_required", "item_poisoned", "processing_failed", "pass"):
        indications = item_gate_result_to_indications(
            SimpleNamespace(status=SimpleNamespace(value=status)),
            model_identity="judge-v1",
            prompt_sha256="a" * 64,
        )
        assert indications == ()


def test_empty_scope_is_exact_frozen_identity_property() -> None:
    candidates = (
        _candidate("a", PipelineAction.RETRIEVAL_ERROR),
        _candidate("b", PipelineAction.SAFETY_ERROR),
    )
    indication = extract_structural_indications("uncovered query", ())
    rng = random.Random(24)
    for _ in range(100):
        frozen = tuple(
            candidate.skill_id
            for candidate in candidates
            if rng.randrange(2)
        )
        decision = route(
            candidates,
            {"a": rng.uniform(-1, 1), "b": rng.uniform(-1, 1)},
            indication,
            ScopePolicy(),
            domain_fingerprint="memfail",
            frozen_selected_ids=frozen,
        )
        assert decision.selected_ids == frozen
        assert decision.frozen_selected_ids == frozen
        assert not decision.routed


def test_active_temporal_scope_overrides_gold_free_selection() -> None:
    candidates = (
        _candidate("a", PipelineAction.RETRIEVAL_ERROR),
        _candidate("b", PipelineAction.ITEM_STALE),
    )
    indications = extract_structural_indications(
        "Where does Alice live?",
        (
            MemoryItem(
                "old",
                "Alice lives in Paris",
                store="2025-01-01T00:00:00Z",
            ),
            MemoryItem(
                "new",
                "Alice lives in Berlin",
                store="2025-02-01T00:00:00Z",
            ),
        ),
    )

    decision = route(
        candidates,
        {"a": 0.8, "b": -0.2},
        indications,
        ScopePolicy.active(
            {RECENCY_SIGNAL},
            domains={RECENCY_SIGNAL: {"memfail"}},
        ),
        domain_fingerprint="memfail",
        frozen_selected_ids=("a",),
    )

    assert decision.selected_ids == ("b",)
    assert decision.routed
    assert decision.signal_type == RECENCY_SIGNAL


def test_scope_ledger_promotes_retires_and_rolls_back() -> None:
    ledger = ScopeLedger(
        n_min=30,
        bootstrap_samples=500,
        seed=7,
    )
    promoted = ledger.audit(
        COLLISION_SIGNAL,
        "memfail",
        [True] * 30,
        incremental_gains=[0.2] * 30,
        family_ids=[f"f{index}" for index in range(30)],
        generation=1,
        provenance={
            "dataset_sha256": "a" * 64,
            "provenance_contract_passed": True,
        },
    )
    assert promoted.decision == "promote"
    assert ledger.get(COLLISION_SIGNAL, "memfail").status == "active"
    assert ledger.to_scope_policy().is_active(COLLISION_SIGNAL, "memfail")

    retired = ledger.audit(
        COLLISION_SIGNAL,
        "memfail",
        [False] * 100,
        incremental_gains=[-0.1] * 100,
        family_ids=[f"r{index}" for index in range(100)],
        generation=2,
        provenance={
            "dataset_sha256": "b" * 64,
            "provenance_contract_passed": True,
        },
    )
    assert retired.decision == "retire"
    assert ledger.get(COLLISION_SIGNAL, "memfail").status == "retired"

    rolled_back = ledger.rollback()
    assert rolled_back[0].status == "active"
    assert rolled_back[0].fires == 30


def test_scope_ledger_round_trip(tmp_path) -> None:
    ledger = ScopeLedger(n_min=3, bootstrap_samples=500)
    ledger.audit(
        COLLISION_SIGNAL,
        "memfail",
        [True, True, True],
        incremental_gains=[0.2, 0.2, 0.2],
        family_ids=["a", "b", "c"],
        generation=1,
        provenance={
            "source": "fixture",
            "provenance_contract_passed": True,
        },
    )
    path = ledger.write(tmp_path / "scope.json")

    restored = ScopeLedger.read(path)

    assert restored.to_dict() == ledger.to_dict()


def test_scope_ledger_requires_positive_incremental_gain() -> None:
    ledger = ScopeLedger(n_min=3, bootstrap_samples=100)
    transition = ledger.audit(
        COLLISION_SIGNAL,
        "memfail",
        [True, True, True],
        incremental_gains=[0.0, 0.0, 0.0],
        family_ids=["a", "b", "c"],
        generation=1,
        provenance={"provenance_contract_passed": True},
    )

    assert transition.decision == "nonpositive_incremental_gain"
    assert ledger.get(COLLISION_SIGNAL, "memfail").status == "shadow"
