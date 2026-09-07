from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments.e4b_descriptor_policy import build_policy_cases, run_e4b
from tests.experiments.test_v4_prequential_runner import _case


def _cases(count: int = 8):
    return tuple(
        _case(index, probe_set="represented", family=f"family-{index}")
        for index in range(count)
    )


def test_v4_cases_map_to_descriptor_random_unkeyed_input_without_labels() -> None:
    rows, assignments, vocabulary = build_policy_cases(
        _cases(),
        candidate_budget=1,
        dev_prefix_fraction=0.25,
        locality_penalty=1.0,
        change_penalty=0.05,
    )
    assert len(rows) == len(assignments) == 8
    assert vocabulary.frozen is True
    assert all(row.descriptor_id.startswith("niche-") for row in rows)
    assert all(row.failure_type == "" for row in rows)
    assert all(item["post_outcome_fields_excluded_from_descriptor"] for item in assignments)


def test_e4b_cli_core_writes_closed_zero_call_artifacts(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(
        "".join(json.dumps(row.to_mapping(), sort_keys=True) + "\n" for row in _cases()),
        encoding="utf-8",
    )
    output = tmp_path / "e4b"
    result = run_e4b(
        cases_path=cases_path,
        output_dir=output,
        candidate_budget=1,
        dev_prefix_fraction=0.25,
        outer_folds=2,
        minimum_training_cases=1,
        minimum_training_families=1,
        minimum_test_families=1,
        bootstrap_samples=100,
        bootstrap_seed=24,
    )
    assert result["headline_arms"] == ["descriptor", "random", "unkeyed"]
    assert result["runtime_uses_gold"] is False
    assert result["model_calls"] == result["network_calls"] == 0
    assert (output / "decision.json").is_file()
    assert (output / "paired_policy_contrasts.csv").is_file()
    assert (output / "ecology_ledger.jsonl").is_file()
    ecology = json.loads((output / "ecology_summary.json").read_text())
    assert ecology["checkpoint_count"] >= 1
    assert ecology["affects_headline_decision"] is False
    assert ecology["model_calls"] == ecology["network_calls"] == 0
    with pytest.raises(ValueError, match="overwrite"):
        run_e4b(
            cases_path=cases_path,
            output_dir=output,
            candidate_budget=1,
            outer_folds=2,
            minimum_training_cases=1,
            minimum_training_families=1,
            minimum_test_families=1,
            bootstrap_samples=100,
        )


def test_e4b_rejects_budget_mismatch_and_unfrozen_future_cluster() -> None:
    with pytest.raises(ValueError, match="budget"):
        build_policy_cases(
            _cases(),
            candidate_budget=2,
            dev_prefix_fraction=0.25,
            locality_penalty=1.0,
            change_penalty=0.05,
        )
    cases = list(_cases())
    last = cases[-1]
    object.__setattr__(last.context, "semantic_cluster", "future-unregistered")
    with pytest.raises(ValueError, match="frozen"):
        build_policy_cases(
            tuple(cases),
            candidate_budget=1,
            dev_prefix_fraction=0.25,
            locality_penalty=1.0,
            change_penalty=0.05,
        )
