from __future__ import annotations

import hashlib
import json
import threading

import pytest

from cmd_audit.counterfactual.actions import PipelineAction
from cmd_audit.counterfactual.operators import OperatorSpec
from cmd_audit.eval.gold_free_observer import ProbeCoordinates
from cmd_audit.repair.skill_ecology import SkillCandidate
from experiments.arena_runner_common import (
    ArenaCase,
    BestOfNControlExecution,
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
        if candidate.skill_id.startswith("confirmation:"):
            gold_free = 0.6
        elif chained:
            gold_free = (
                0.6
                if input_context.endswith("->a")
                and candidate.skill_id == "b"
                else 0.1
            )
        else:
            gold_free = standalone
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

    def evaluate_best_of_n(self, case, *, candidate_count, origin_context):
        del case, origin_context
        return BestOfNControlExecution(
            candidate_count=candidate_count,
            finite_candidate_count=candidate_count,
            selected_index=0,
            selection_gain=0.15,
            shadow_gold_gain=0.2,
            answer_calls=candidate_count,
            selection_judge_calls=candidate_count,
        )


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


def test_manifest_binds_source_bytes_and_ordered_selected_cases(tmp_path) -> None:
    source = tmp_path / "cases.json"
    source.write_text('[{"case_id":"source-case"}]\n', encoding="utf-8")
    cases = _cases(2)

    result = ObservationalArenaRunner(
        cases,
        backend=FakeDualBackend(),
        enable_chains=False,
        dataset_source_path=source,
    ).run()

    manifest = result.manifest
    assert manifest.dataset_fingerprint_version == "arena-dataset-v1"
    assert manifest.dataset_source_kind == "file"
    assert manifest.dataset_source_path == str(source.resolve())
    assert manifest.dataset_source_sha256 == hashlib.sha256(
        source.read_bytes()
    ).hexdigest()
    assert manifest.dataset_source_size_bytes == source.stat().st_size
    assert len(manifest.selected_case_ids_sha256) == 64
    assert len(manifest.selected_cases_sha256) == 64

    reversed_result = ObservationalArenaRunner(
        tuple(reversed(cases)),
        backend=FakeDualBackend(),
        enable_chains=False,
        dataset_source_path=source,
    ).run()
    assert (
        reversed_result.manifest.selected_case_ids_sha256
        != manifest.selected_case_ids_sha256
    )
    assert (
        reversed_result.manifest.selected_cases_sha256
        != manifest.selected_cases_sha256
    )


def test_deposition_requires_and_calls_backend_hook(tmp_path):
    backend = FakeDualBackend()
    result = ObservationalArenaRunner(
        _cases(20),
        backend=backend,
        saturation_threshold=0.25,
        deposition_after_fraction=0.5,
    ).run()
    assert len(result.deposition_events) == 1
    assert any(row.passed for row in result.deposition_candidate_events)
    assert result.deposition_confirmation_events[0].d2_passed
    assert result.deposition_confirmation_events[0].d3_passed
    assert result.manifest.deposition_confirmation_calls == 24
    assert result.manifest.deposition_confirmation_calls <= 50
    assert backend.depositions == list(result.deposition_events)
    forward_attempts = sum(
        (row.first_skill_id, row.second_skill_id) == ("a", "b")
        for row in result.chain_attempts
    )
    reverse_attempts = sum(
        (row.first_skill_id, row.second_skill_id) == ("b", "a")
        for row in result.chain_attempts
    )
    assert reverse_attempts < forward_attempts
    output = write_arena_artifacts(result, tmp_path / "deposition.jsonl")
    record_types = {
        json.loads(line)["record_type"]
        for line in output.read_text(encoding="utf-8").splitlines()
    }
    assert {
        "deposition_candidate_event",
        "deposition_confirmation_event",
        "chain_deposition_event",
        "anti_pattern_event",
    } <= record_types

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


def test_fill_cases_are_explicit_routed_abstentions() -> None:
    backend = FakeDualBackend()
    fill = ArenaCase(
        **{
            **_cases(1)[0].__dict__,
            "runtime_branch": "fill",
            "hook_confidence": 0.1,
        }
    )

    result = ObservationalArenaRunner((fill,), backend=backend).run()

    assert backend.inputs == []
    assert result.manifest.fill_case_count == 1
    assert result.manifest.fix_case_count == 0
    assert result.saturation_events[0].runtime_branch == "fill"
    assert result.saturation_events[0].attempted_skill_ids == ()
    assert not result.saturation_events[0].repair_effective


def test_stateless_arena_runs_cases_concurrently_and_reduces_in_order() -> None:
    class ConcurrentBackend(FakeDualBackend):
        def __init__(self):
            super().__init__()
            self.barrier = threading.Barrier(2)
            self.first_seen: set[str] = set()

        def evaluate(self, case, candidate, *, input_context, origin_context):
            if case.case_id not in self.first_seen:
                self.first_seen.add(case.case_id)
                self.barrier.wait(timeout=2)
            return super().evaluate(
                case,
                candidate,
                input_context=input_context,
                origin_context=origin_context,
            )

    result = ObservationalArenaRunner(
        _cases(2),
        backend=ConcurrentBackend(),
        enable_chains=False,
        case_workers=2,
    ).run()

    assert result.manifest.case_workers == 2
    assert [row.case_id for row in result.saturation_events] == ["c0", "c1"]


def test_cross_case_concurrency_rejects_stateful_stream_interventions() -> None:
    with pytest.raises(ValueError, match="cross-case concurrency"):
        ObservationalArenaRunner(
            _cases(4),
            backend=FakeDualBackend(),
            case_workers=2,
            deposition_after_fraction=0.5,
        )


def test_best_of_n_control_is_serialized_as_budget_aligned_single_winner() -> None:
    result = ObservationalArenaRunner(
        _cases(1),
        backend=FakeDualBackend(),
        enable_chains=False,
        enable_best_of_n_control=True,
    ).run()

    event = result.arm_comparison_events[0]
    assert event.candidate_budget == 2
    assert event.cmd_selected_skill_id == "a"
    assert event.best_of_n_selected_index == 0
    assert event.budget_aligned
    assert event.cmd_answer_calls == event.best_of_n_answer_calls == 2


def test_cmd_abstention_is_missing_not_a_finite_zero_shadow_gain() -> None:
    class AbstainingBackend(FakeDualBackend):
        def evaluate(
            self,
            case,
            candidate,
            *,
            input_context,
            origin_context,
        ):
            row = super().evaluate(
                case,
                candidate,
                input_context=input_context,
                origin_context=origin_context,
            )
            return DualScoreExecution(
                skill_id=row.skill_id,
                repaired_context=row.repaired_context,
                gold_free_gain=-0.1,
                shadow_gold_gain=0.8,
                execution_cost=row.execution_cost,
            )

    result = ObservationalArenaRunner(
        _cases(1),
        backend=AbstainingBackend(),
        enable_chains=False,
        enable_best_of_n_control=True,
    ).run()

    event = result.arm_comparison_events[0]
    assert event.cmd_abstained
    assert event.cmd_selected_skill_id is None
    assert event.cmd_shadow_gold_gain is None


def test_evolving_selector_is_strictly_test_then_update() -> None:
    backend = FakeDualBackend()
    backend.base_candidates = list(reversed(backend.base_candidates))

    result = ObservationalArenaRunner(
        _cases(2),
        backend=backend,
        candidate_limit=2,
        enable_chains=False,
        evolve_selection_priors=True,
    ).run()

    assert result.saturation_events[0].attempted_skill_ids[0] == "a"
    # With no prior both skills tie and stable id order applies; only case two
    # can consume case one's gold-free evidence.
    assert result.saturation_events[1].attempted_skill_ids[0] == "a"
    assert result.manifest.selector_evolution_enabled


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
