from pathlib import Path

from experiments.run_evolution_governance_phase0 import (
    OfflineArenaRun,
    replay_d1,
    replay_selector,
)


def _run() -> OfflineArenaRun:
    observations = []
    attempts = []
    for position in range(1, 13):
        case_id = f"c{position}"
        family_id = f"f{position % 3}"
        observations.append(
            {
                "case_id": case_id,
                "family_id": family_id,
                "gold_free_scores": [["seed:retrieval_error", 0.4], ["seed:injection_error", 0.0]],
                "shadow_gold_scores": [["seed:retrieval_error", 0.5], ["seed:injection_error", 0.0]],
            }
        )
        attempts.extend(
            (
                {
                    "case_id": case_id,
                    "failure_type": "retrieval_error",
                    "stream_position": position,
                    "first_skill_id": "seed:retrieval_error",
                    "second_skill_id": "seed:injection_error",
                    "chain_benefit": 0.3,
                    "chained_gain": 0.7,
                    "standalone_max": 0.4,
                    "status": "ok",
                },
                {
                    "case_id": case_id,
                    "failure_type": "retrieval_error",
                    "stream_position": position,
                    "first_skill_id": "seed:injection_error",
                    "second_skill_id": "seed:retrieval_error",
                    "chain_benefit": -0.1,
                    "chained_gain": 0.3,
                    "standalone_max": 0.4,
                    "status": "ok",
                },
            )
        )
    return OfflineArenaRun(
        run_id="fixture",
        path=Path("fixture.jsonl"),
        manifest={
            "arena_id": "memtrace",
            "case_count": 12,
            "runtime_uses_gold": False,
        },
        observations=tuple(observations),
        attempts=tuple(attempts),
        deposition_after_case=12,
        artifact_sha256="a" * 64,
    )


def test_phase0_replays_selector_prequentially() -> None:
    summary, curve = replay_selector(_run(), seed=24, permutations=99)

    assert summary["prequential"] is True
    assert curve[0]["evolving_skill_id"] == curve[0]["frozen_skill_id"]
    assert curve[-1]["evolving_cumulative_shadow_gain"] >= curve[-1]["frozen_cumulative_shadow_gain"]


def test_phase0_reconstructs_family_aware_d1() -> None:
    events, _benefits = replay_d1(
        _run(),
        seed=24,
        bootstrap_samples=500,
    )
    forward = next(
        row
        for row in events
        if row.first_skill_id == "seed:retrieval_error"
    )

    assert forward.passed
    assert forward.n_clusters == 3
    assert forward.source_sha256 == "a" * 64
