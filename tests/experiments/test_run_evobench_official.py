from pathlib import Path

import pytest

from experiments.run_evobench_official import build_command


def test_evolve_command_matches_official_budget_and_suites() -> None:
    root = Path("/official/Evo-Bench")
    command = build_command(
        stage="evolve",
        root=root,
        policy_config=Path("policy.json"),
        judge_config=Path("judge.json"),
        evolver_config=Path("evolver.json"),
    )
    joined = " ".join(command)
    assert "run-evolve" in command
    assert "evobench_validation.json" in joined
    assert "evobench_evaluation.json" in joined
    assert command[command.index("--max-iterations") + 1] == "20"
    assert command[command.index("--max-steps") + 1] == "1000"
    assert command[command.index("--sandbox-ttl-minutes") + 1] == "2880"
    assert command[command.index("--trials-by-domain") + 1] == "general=3"


def test_evaluation_requires_frozen_harness() -> None:
    with pytest.raises(ValueError, match="frozen_harness"):
        build_command(
            stage="evaluation",
            root=Path("/official/Evo-Bench"),
            policy_config=Path("policy.json"),
            judge_config=Path("judge.json"),
        )
