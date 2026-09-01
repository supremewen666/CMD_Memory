from __future__ import annotations

from cmd_audit.spec_v03.governance_system_executor import ExecutionOrder, GovernanceSystemExecutor
from cmd_audit.spec_v03.repair_stream import execute_operator, operator_catalog
from cmd_audit.spec_v03.sealed_governance import (
    FrozenSelectionProposalProvider,
    score_governance_records,
)
from tests.spec_v03.test_governance_system_executor import _bundle


def test_frozen_selection_replay_scores_full_governance_against_sealed_root() -> None:
    bundle = _bundle()
    provider = FrozenSelectionProposalProvider({bundle.case_id: "process_restore"})
    record = GovernanceSystemExecutor().execute_stage7(
        bundle, ExecutionOrder(0), provider,
        variant="full_governance", run_id="sealed-test",
    )
    spec = next(item for item in operator_catalog() if item.operator_id == "process_restore")
    expected = execute_operator(bundle.memory_state, spec).root
    scored = score_governance_records(
        (record,),
        {bundle.case_id: {
            "incident_type": "process_fault",
            "expected_state_root": expected,
            "legal_operator_ids": ("process_restore",),
        }},
        {bundle.case_id: bundle.family_id},
    )
    metric = scored["variant_metrics"][0]
    assert metric["safe_repair_success_rate"] == 1.0
    assert metric["false_commit_rate"] == 0.0
    assert scored["rows"][0]["selected_operator_legal"] is True
