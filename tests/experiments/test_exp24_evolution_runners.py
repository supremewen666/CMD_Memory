from __future__ import annotations

import json

import pytest

from cmd_audit.counterfactual.actions import PipelineAction
from cmd_audit.counterfactual.operators import OperatorSpec
from cmd_audit.eval.evolution_gates import FamilyNetGains, family_bucket
from cmd_audit.repair.operator_library import OperatorSpecRecord
from experiments.evolution_runner_common import (
    OfflineEvolutionRunner,
    PrequentialEvolutionRunner,
    write_run_artifacts,
)
from experiments.run_experiment_24a_offline_evolution import FixtureBackend
from experiments.run_experiment_24b_prequential_evolution import (
    _experiment_a_gate_status,
    _experiment_a_scorer_version,
)


def test_experiment_b_fails_closed_until_both_a_gates_pass():
    with pytest.raises(RuntimeError, match="both Experiment A Gates"):
        PrequentialEvolutionRunner(
            (),
            experiment_a_primary_passed=True,
            experiment_a_safety_passed=False,
            case_evaluator=lambda *_args: None,
            shadow_discoverer=lambda *_args: (),
            probe_evaluator=lambda *_args: False,
            scorer_version="judge-v1",
            discovery_config={},
        )


def test_experiment_b_rejects_legacy_gate_without_within_family(
    tmp_path,
):
    gate_path = tmp_path / "gate_results.json"
    gate_path.write_text(
        json.dumps(
            {
                "primary": {"passed": True},
                "safety": {"passed": True},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="expected Experiment A"):
        _experiment_a_gate_status(gate_path)


def test_experiment_b_requires_combined_within_family_gate_to_pass(
    tmp_path,
):
    gate_path = tmp_path / "gate_results.json"
    gate_path.write_text(
        json.dumps(
            {
                "primary": {"passed": True},
                "safety": {"passed": True},
                "within_family": {"combined": {"passed": False}},
            }
        ),
        encoding="utf-8",
    )

    assert _experiment_a_gate_status(gate_path) == (False, True)


def test_experiment_b_reads_frozen_scorer_from_a_manifest(tmp_path):
    gate_path = tmp_path / "gate_results.json"
    gate_path.write_text("{}", encoding="utf-8")
    (tmp_path / "run_manifest.json").write_text(
        json.dumps({"scorer_version": "derived-judge-id"}),
        encoding="utf-8",
    )

    assert _experiment_a_scorer_version(gate_path) == "derived-judge-id"


def test_experiment_b_rejects_gate_without_scorer_manifest(tmp_path):
    gate_path = tmp_path / "gate_results.json"
    gate_path.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="run manifest is required"):
        _experiment_a_scorer_version(gate_path)


def _family_id(*, represented: bool, start: int) -> str:
    for index in range(start, start + 10_000):
        candidate = f"family-{index}"
        if (family_bucket(candidate) != 0) == represented:
            return candidate
    raise AssertionError("could not construct deterministic family id")


def test_runner_promotes_stable_revisions_and_audits_displacement(tmp_path):
    represented = (
        _family_id(represented=True, start=1),
        _family_id(represented=True, start=100),
    )
    unseen = _family_id(represented=False, start=200)
    rows = [
        {
            "case_id": f"{family}-v{variant}",
            "recurrent_family_id": family,
            "recurrent_variant_index": variant,
            "extracted_memory": [{"text": "shared recurrent memory"}],
        }
        for family in (*represented, unseen)
        for variant in range(5)
    ]
    spec = OperatorSpec.single(0, PipelineAction.RETRIEVAL_ERROR)
    spec_hash = OperatorSpecRecord.from_operator(spec).spec_hash
    cases = {
        str(row["case_id"]): {
            "shadow_candidates": [
                {
                    "spec": spec.to_dict(),
                    "recovery_gain": 0.3,
                    "rollout_cost": 1.0,
                }
            ],
            "arms": {
                arm: {"spec_gains": {spec_hash: 0.3}}
                for arm in ("patterned", "unkeyed_global")
            },
        }
        for row in rows
    }
    backend = FixtureBackend({"cases": cases})
    runner = OfflineEvolutionRunner(
        rows,
        case_evaluator=backend.evaluate,
        shadow_discoverer=backend.shadow,
        probe_evaluator=backend.probe,
        direct_revision_evaluator=backend.direct_revision_gain,
        scorer_version="fixture-judge-v1",
        discovery_config={"source": "fixture"},
        seed=24,
    )
    result = runner.run(
        within_family_gains=(
            FamilyNetGains("kp-1", "kp", (0.0,), (0.3,)),
            FamilyNetGains("slug-1", "slug", (0.0,), (0.3,)),
        ),
        bootstrap_samples=100,
    )

    assert result.store.head("patterned").stable_revision_ids
    assert result.store.head("unkeyed_global").stable_revision_ids
    assert len(result.store.anchor_sets) == 2
    assert all(
        len(
            {
                anchor.producing_case_id,
                *anchor.validation_case_ids,
            }
        )
        == 4
        for anchor in result.store.anchor_sets.values()
    )
    assert result.retrieval_displacement
    assert all(dict(result.leakage_assertions).values())
    output = write_run_artifacts(
        result,
        tmp_path,
        run_manifest={"test": True},
    )
    assert (output / "retrieval_displacement.jsonl").read_text(
        encoding="utf-8"
    ).strip()
    assertions = json.loads(
        (output / "leakage_assertions.json").read_text(encoding="utf-8")
    )
    assert assertions
    assert all(assertions.values())
