from __future__ import annotations

import json

import pytest

from cmd_audit.counterfactual.actions import PipelineAction
from cmd_audit.counterfactual.operators import OperatorSpec
from cmd_audit.eval.gold_free_observer import ProbeCoordinates
from cmd_audit.repair.skill_ecology import SkillCandidate
from experiments.arena_runner_common import (
    ArenaCase,
    DualScoreExecution,
    ObservationalArenaRunner,
    load_memfail_arena_cases,
    load_memtrace_arena_cases,
    load_stale_arena_cases,
    write_arena_artifacts,
)


class FakeDualBackend:
    gold_free_signal_name = "fixture_gold_free"
    shadow_gold_signal_name = "fixture_shadow_gold"
    runtime_uses_gold = False

    def __init__(self):
        self.base_candidates = [
            SkillCandidate(
                "a",
                OperatorSpec.single(0, PipelineAction.RETRIEVAL_ERROR),
            ),
            SkillCandidate(
                "b",
                OperatorSpec.single(0, PipelineAction.INJECTION_ERROR),
            ),
        ]
        self.depositions = []
        self.inputs = []

    def candidates(self, _case):
        return tuple(self.base_candidates)

    def evaluate(
        self,
        case,
        candidate,
        *,
        input_context,
        origin_context,
    ):
        self.inputs.append((case.case_id, candidate.skill_id, input_context))
        chained = input_context != origin_context
        standalone = 0.2 if candidate.skill_id == "a" else 0.1
        gold_free = 0.6 if chained else standalone
        shadow = 0.7 if candidate.skill_id == "a" else 0.3
        return DualScoreExecution(
            skill_id=candidate.skill_id,
            repaired_context=f"{input_context}->{candidate.skill_id}",
            gold_free_gain=gold_free,
            shadow_gold_gain=shadow,
            execution_cost=1.0,
        )

    def deposit_composite(self, event):
        self.depositions.append(event)


def _cases(count=4):
    return tuple(
        ArenaCase(
            arena_id="fixture",
            case_id=f"c{index}",
            family_id=f"f{index // 2}",
            failure_type="retrieval_error" if index else "null",
            base_context=f"base-{index}",
            coordinates=ProbeCoordinates(index, "current", "present"),
            subset="fixture",
            raw={},
        )
        for index in range(count)
    )


def test_arena_runs_one_path_and_observers_do_not_change_selection(tmp_path):
    backend = FakeDualBackend()
    result = ObservationalArenaRunner(
        _cases(),
        backend=backend,
        saturation_threshold=0.25,
        enable_chains=True,
    ).run()
    assert len(result.gold_free_observations) == 4
    assert all(
        row.selected_skill_ids == ("a", "b")
        for row in result.saturation_events
    )
    assert all(row.covered for row in result.saturation_events)
    assert len(result.chain_attempts) == 8
    assert result.manifest.runtime_uses_gold is False
    standalone_inputs = [
        value for value in backend.inputs if "->" not in value[2]
    ]
    assert len(standalone_inputs) == 8
    assert all(
        input_context == f"base-{case_id[1:]}"
        for case_id, _skill, input_context in standalone_inputs
    )

    path = write_arena_artifacts(result, tmp_path / "arena.jsonl")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[0]["record_type"] == "arena_manifest"
    assert sum(row["record_type"] == "gold_free_observation" for row in rows) == 4
    assert sum(row["record_type"] == "top_p_saturation_event" for row in rows) == 4
    assert not any(row["record_type"] == "competition_event" for row in rows)


def test_deposition_requires_and_calls_backend_hook():
    backend = FakeDualBackend()
    result = ObservationalArenaRunner(
        _cases(6),
        backend=backend,
        saturation_threshold=0.25,
        deposition_after_fraction=0.5,
        deposition_min_support=3,
    ).run()
    assert len(result.deposition_events) == 1
    assert backend.depositions == list(result.deposition_events)

    class MissingHook(FakeDualBackend):
        deposit_composite = None

    with pytest.raises(ValueError, match="deposit_composite"):
        ObservationalArenaRunner(
            _cases(),
            backend=MissingHook(),
            deposition_after_fraction=0.5,
        )


def test_gold_dependent_runtime_backend_is_rejected():
    backend = FakeDualBackend()
    backend.runtime_uses_gold = True
    with pytest.raises(ValueError, match="gold-dependent"):
        ObservationalArenaRunner(_cases(), backend=backend)


def test_perturbation_removes_keystone_and_records_recovery(tmp_path):
    backend = FakeDualBackend()
    result = ObservationalArenaRunner(
        _cases(8),
        backend=backend,
        saturation_threshold=0.25,
        enable_chains=False,
        perturb_after_fraction=0.25,
        perturb_strategy="keystone",
        perturb_window_size=2,
        perturb_stable_windows=1,
        perturb_stability_threshold=0.0,
    ).run()
    event = result.perturbation_events[0]
    assert event.removed_skill_id == "a"
    assert event.started_after_case == 2
    assert event.recovered_after_cases == 4
    by_case = {}
    for case_id, skill_id, _context in backend.inputs:
        by_case.setdefault(case_id, set()).add(skill_id)
    assert "a" in by_case["c0"] and "a" in by_case["c1"]
    assert all("a" not in by_case[f"c{index}"] for index in range(2, 8))
    artifact = write_arena_artifacts(result, tmp_path / "perturbed.jsonl")
    rows = [json.loads(line) for line in artifact.read_text().splitlines()]
    assert sum(row["record_type"] == "perturbation_event" for row in rows) == 1


def test_dataset_specific_stream_loaders_recover_observational_dimensions():
    memtrace = load_memtrace_arena_cases(
        "data/probe_cases/memtrace_kp_cases.json",
        seed=24,
        limit=8,
    )
    assert len(memtrace) == 8
    assert all(row.family_id and row.coordinates.age_sessions is not None for row in memtrace)
    assert all(row.coordinates.evidence_condition for row in memtrace)

    memfail = load_memfail_arena_cases(
        "data/probe_cases/memfail_cases.json",
        seed=24,
        limit=8,
    )
    assert len(memfail) == 8
    assert all(row.subset != "unknown" for row in memfail)

    stale = load_stale_arena_cases(
        "data/probe_cases/stale_item_cases.json",
        seed=24,
        limit=8,
    )
    assert len(stale) == 8
    assert {row.failure_type for row in stale} <= {
        "item_stale",
        "item_conflict",
    }
