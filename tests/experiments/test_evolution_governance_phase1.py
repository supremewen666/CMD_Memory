from __future__ import annotations

import json
from pathlib import Path
import sys

from experiments.run_evolution_governance_phase1 import (
    main,
    merge_phase1_summaries,
)


def test_phase1_validation_can_select_one_arena(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    phase0 = tmp_path / "phase0.json"
    phase0.write_text(
        json.dumps({"phase1_gate_passed": True}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "phase1",
            "--phase0-summary",
            str(phase0),
            "--arena",
            "memtrace",
            "--limit",
            "1",
        ],
    )

    assert main() == 0
    output = capsys.readouterr().out
    assert "[RESULT] memtrace_cases=1" in output
    assert "stale_cases" not in output
    assert "[RESULT] model_calls=0" in output


def test_merge_phase1_summaries_requires_disjoint_complete_arenas(
    tmp_path: Path,
) -> None:
    paths = []
    for arena_id, passed in (("memtrace", True), ("stale", False)):
        path = tmp_path / f"{arena_id}.json"
        path.write_text(
            json.dumps(
                {
                    "phase": 1,
                    "seed": 24,
                    "candidate_budget": 2,
                    "phase0_summary_sha256": "a" * 64,
                    "arenas": [
                        {
                            "arena_id": arena_id,
                            "g_e2_passed": True,
                            "g_e3_passed": passed,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        paths.append(path)

    merged = merge_phase1_summaries(
        tuple(paths),
        output_dir=tmp_path / "combined",
    )

    assert merged["g_e2_passed"] is True
    assert merged["g_e3_passed"] is False
    assert merged["decision"] == "negative_result_chapter"
    assert (
        tmp_path / "combined" / "phase1_combined_summary.json"
    ).is_file()


def test_dual_gpu_driver_routes_phase1_by_arena() -> None:
    script = (
        Path(__file__).parents[2] / "run_remaining_experiments.sh"
    ).read_text(encoding="utf-8")

    assert 'run_phase1_arena "memtrace" "gpu0"' in script
    assert 'run_phase1_arena "stale" "gpu1"' in script
    assert "--merge-summaries" in script
    assert "--deposit-min-support 10" in script
    assert "--deposit-min-support 3" not in script
