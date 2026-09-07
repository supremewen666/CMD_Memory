from __future__ import annotations

import json

import pytest

from cmd_audit.eval.descriptor_policy_value import (
    DescriptorPolicyCase,
    evaluate_descriptor_policy_value,
)
from experiments.analyze_descriptor_policy_value import (
    build_descriptor,
    load_v0_input,
    main,
)


def _separable_cases(
    *,
    families_per_niche: int = 50,
) -> tuple[DescriptorPolicyCase, ...]:
    rows = []
    for niche, winner in (("signal:a", "skill:a"), ("signal:b", "skill:b")):
        for index in range(families_per_niche):
            gains = (
                ("skill:a", 1.0 if winner == "skill:a" else 0.0),
                ("skill:b", 1.0 if winner == "skill:b" else 0.0),
            )
            rows.append(
                DescriptorPolicyCase(
                    case_id=f"{niche}-{index}",
                    family_id=f"{niche}-family-{index}",
                    domain_id="stale",
                    descriptor_id=niche,
                    runtime_branch="fix",
                    candidate_gains=gains,
                    frozen_skill_id=None,
                    frozen_gain=0.0,
                )
            )
    for index in range(10):
        rows.append(
            DescriptorPolicyCase(
                case_id=f"fill-{index}",
                family_id=f"fill-family-{index}",
                domain_id="stale",
                descriptor_id="no-signal",
                runtime_branch="fill",
                candidate_gains=(),
                frozen_skill_id=None,
                frozen_gain=0.0,
            )
        )
    return tuple(rows)


def test_cross_fitted_descriptor_policy_can_pass_all_v0_gates() -> None:
    decision, predictions = evaluate_descriptor_policy_value(
        _separable_cases(),
        outer_folds=5,
        minimum_training_cases=20,
        minimum_training_families=20,
        minimum_test_families=5,
        bootstrap_samples=200,
        bootstrap_seed=9,
    )

    assert decision.final_decision == "GO"
    domain = decision.domains[0]
    assert domain.verdict == "GO"
    assert domain.distinct_stable_elites == 2
    assert domain.descriptor_vs_frozen.passed
    assert domain.descriptor_vs_unkeyed.passed
    assert domain.descriptor_vs_random.passed
    assert domain.null_fill_exact
    assert all(
        row.descriptor_matches_frozen
        for row in predictions
        if row.runtime_branch == "fill"
    )


def test_one_supported_niche_is_insufficient_not_go() -> None:
    cases = tuple(
        DescriptorPolicyCase(
            case_id=f"c{index}",
            family_id=f"f{index}",
            domain_id="stale",
            descriptor_id="only",
            runtime_branch="fix",
            candidate_gains=(("skill:a", 1.0), ("skill:b", 0.0)),
            frozen_skill_id=None,
            frozen_gain=0.0,
        )
        for index in range(50)
    )
    decision, _predictions = evaluate_descriptor_policy_value(
        cases,
        minimum_training_cases=20,
        minimum_training_families=20,
        minimum_test_families=5,
        bootstrap_samples=100,
    )

    assert decision.final_decision == "INSUFFICIENT_SUPPORT"
    assert decision.domains[0].verdict == "INSUFFICIENT_SUPPORT"


def test_descriptor_ignores_indication_action() -> None:
    base = {
        "signal_type": "temporal_content_contradiction",
        "strength": 0.99,
        "runtime_surface": "tier2_item_gate",
        "extractor_version": "v1",
        "input_allowlist_sha256": "a" * 64,
    }
    first, first_meta = build_descriptor(({**base, "action": "item_stale"},))
    second, second_meta = build_descriptor(
        ({**base, "action": "item_conflict"},)
    )

    assert first == second
    assert first_meta["action_field_ignored"] is True
    assert second_meta["action_field_ignored"] is True


def test_load_v0_input_rejects_activated_route(tmp_path) -> None:
    artifact = tmp_path / "activated.jsonl"
    rows = (
        {
            "record_type": "arena_manifest",
            "arena_id": "stale",
            "runtime_uses_gold": False,
        },
        {
            "record_type": "gold_free_observation",
            "case_id": "c1",
            "family_id": "f1",
            "selected_skill_id": None,
            "selected_shadow_gain": None,
            "shadow_gold_scores": [["skill:a", 1.0]],
        },
        {
            "record_type": "top_p_saturation_event",
            "case_id": "c1",
            "runtime_branch": "fix",
        },
        {
            "record_type": "structural_indication_event",
            "case_id": "c1",
            "runtime_surface": "tier2_item_gate",
            "signal_type": "temporal_content_contradiction",
            "strength": 1.0,
            "created_before_outcome": True,
            "route_selected": True,
            "scope_active": True,
        },
    )
    artifact.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="shadow indication"):
        load_v0_input(artifact)


def test_load_v0_input_excludes_invalid_safety_candidate(tmp_path) -> None:
    artifact = tmp_path / "safety.jsonl"
    rows = (
        {
            "record_type": "arena_manifest",
            "arena_id": "memfail",
            "runtime_uses_gold": False,
        },
        {
            "record_type": "gold_free_observation",
            "case_id": "c1",
            "family_id": "f1",
            "selected_skill_id": None,
            "selected_shadow_gain": None,
            "shadow_gold_scores": [
                ["seed:safety_error", 1.0],
                ["seed:retrieval_error", 0.2],
            ],
        },
        {
            "record_type": "top_p_saturation_event",
            "case_id": "c1",
            "runtime_branch": "fix",
        },
    )
    artifact.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    _manifest, cases, _metadata = load_v0_input(artifact)

    assert cases[0].candidate_gains == (("seed:retrieval_error", 0.2),)


def test_cli_writes_mechanical_v0_artifacts(tmp_path, monkeypatch) -> None:
    artifact = tmp_path / "arena.jsonl"
    rows = [
        {
            "record_type": "arena_manifest",
            "arena_id": "stale",
            "runtime_uses_gold": False,
            "structural_extractor_version": "live-item-gate-v1",
        }
    ]
    for niche, signal, winner in (
        ("a", "temporal_content_contradiction", "skill:a"),
        ("b", "recall_set_collision", "skill:b"),
    ):
        for index in range(30):
            case_id = f"{niche}-{index}"
            rows.extend(
                [
                    {
                        "record_type": "gold_free_observation",
                        "case_id": case_id,
                        "family_id": f"{niche}-family-{index}",
                        "failure_type": "item_stale",
                        "selected_skill_id": None,
                        "selected_shadow_gain": None,
                        "shadow_gold_scores": [
                            [
                                "skill:a",
                                1.0 if winner == "skill:a" else 0.0,
                            ],
                            [
                                "skill:b",
                                1.0 if winner == "skill:b" else 0.0,
                            ],
                        ],
                    },
                    {
                        "record_type": "top_p_saturation_event",
                        "case_id": case_id,
                        "runtime_branch": "fix",
                    },
                    {
                        "record_type": "structural_indication_event",
                        "case_id": case_id,
                        "runtime_surface": "tier2_item_gate",
                        "signal_type": signal,
                        "strength": 0.99,
                        "created_before_outcome": True,
                        "route_selected": False,
                        "scope_active": False,
                        "extractor_version": "v1",
                        "input_allowlist_sha256": "a" * 64,
                        "action": "ignored",
                    },
                ]
            )
    for index in range(5):
        case_id = f"fill-{index}"
        rows.extend(
            [
                {
                    "record_type": "gold_free_observation",
                    "case_id": case_id,
                    "family_id": f"fill-family-{index}",
                    "selected_skill_id": None,
                    "selected_shadow_gain": None,
                    "shadow_gold_scores": [],
                },
                {
                    "record_type": "top_p_saturation_event",
                    "case_id": case_id,
                    "runtime_branch": "fill",
                },
            ]
        )
    artifact.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    output = tmp_path / "v0"
    monkeypatch.setattr(
        "sys.argv",
        [
            "analyze_descriptor_policy_value",
            "--inputs",
            str(artifact),
            "--output-dir",
            str(output),
            "--minimum-training-cases",
            "10",
            "--minimum-training-families",
            "10",
            "--minimum-test-families",
            "2",
            "--bootstrap-samples",
            "100",
        ],
    )

    assert main() == 0
    decision = json.loads(
        (output / "v0_claim_decision.json").read_text(encoding="utf-8")
    )
    assert decision["final_decision"] == "GO"
    for name in (
        "v0_manifest.json",
        "descriptor_occupancy.csv",
        "descriptor_stability.json",
        "operator_actuator_audit.csv",
        "oracle_headroom_by_scope.csv",
        "elite_heterogeneity.csv",
        "crossfit_policy_predictions.jsonl",
        "paired_policy_contrasts.csv",
        "protected_scope_gates.csv",
    ):
        assert (output / name).is_file()
