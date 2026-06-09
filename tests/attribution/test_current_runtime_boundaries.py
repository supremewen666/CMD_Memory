from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cmd_audit import (
    FailureMemoryStore,
    load_probe_cases,
    run_case,
    run_replay_baseline_case,
)
from cmd_audit.attribution import (
    FAILURE_REASON_OUT_OF_SCOPE_REPLAY,
    assign_attribution,
)
from cmd_audit.mcts import PipelineAction
from cmd_audit.replays import ReplayResult


FIXTURE = Path("data/probe_cases/v0_retrieval_error_case.json")


def _replay(name: str, gain: float) -> ReplayResult:
    return ReplayResult(
        replay_name=name,
        answer="",
        answer_score=0.0,
        evidence_score=0.0,
        evidence_block="",
        recovery_gain=gain,
    )


def test_replay_ranking_abstains_when_removed_replay_wins() -> None:
    result = assign_attribution(
        (
            _replay("oracle_write", 1.0),
            _replay("oracle_retrieval", 0.6),
        )
    )

    assert result.attribution_failed is True
    assert result.failure_reason == FAILURE_REASON_OUT_OF_SCOPE_REPLAY
    assert result.predicted_label == ""


def test_replay_ranking_uses_only_current_step_actions() -> None:
    result = assign_attribution(
        (
            _replay("oracle_retrieval", 0.9),
            _replay("injection_oracle", 0.85),
            _replay("evidence_given_reasoning", 0.1),
        ),
        tie_margin=0.1,
        top_k=2,
    )

    assert result.predicted_label == "retrieval_error"
    assert result.top2_labels == ("retrieval_error", "injection_error")
    assert "reasoning_error" not in result.top_k_labels


def test_run_replay_baseline_case_is_explicit_offline_path() -> None:
    case = load_probe_cases(FIXTURE)[0]

    result = run_replay_baseline_case(case)

    assert result.runtime_branch == "offline_replay"


def test_run_case_writes_mcts_result_to_step_level_failure_memory() -> None:
    case = load_probe_cases(FIXTURE)[0]
    store = FailureMemoryStore()
    mcts_result = SimpleNamespace(
        main_culprit=(1, PipelineAction.INJECTION_ERROR, 0.7),
        primary_attribution_label=PipelineAction.INJECTION_ERROR,
        action_credits={
            1: {
                PipelineAction.IDENTITY: 0.1,
                PipelineAction.INJECTION_ERROR: 0.8,
            }
        },
    )

    with patch("cmd_audit.harness.run_mcts_attribution", return_value=mcts_result):
        result = run_case(case, failure_memory_store=store)

    assert result.runtime_branch == "fix"
    assert len(store) == 1
    records = store.retrieve(
        query=case.query,
        hop_index=1,
        label="injection_error",
    )
    assert records
    assert records[0].error_type == "injection_error"
    assert records[0].recovery_success is True
    assert store.get_label_prior("injection_error") == pytest.approx(1.0)
