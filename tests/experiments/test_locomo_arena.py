from __future__ import annotations

import json
from pathlib import Path

from experiments.locomo_arena import load_locomo_arena_cases, runtime_projection


def _fixture(path: Path, answer: str = "blue") -> Path:
    path.write_text(json.dumps([{
        "sample_id": "s1",
        "conversation": {
            "speaker_a": "A",
            "speaker_b": "B",
            "session_1_date_time": "1 Jan 2024",
            "session_1": [{"speaker": "A", "dia_id": "D1:1", "text": "My bike is blue."}],
            "session_2_date_time": "2 Jan 2024",
            "session_2": [{"speaker": "B", "dia_id": "D2:1", "text": "We went hiking."}],
        },
        "qa": [{"question": "What color is A's bike?", "answer": answer, "category": 4, "evidence": ["D1:1"]}],
        "observation": {"secret": answer},
        "session_summary": {"secret": answer},
    }]), encoding="utf-8")
    return path


def test_locomo_projection_is_gold_and_evidence_invariant(tmp_path: Path) -> None:
    first = load_locomo_arena_cases(_fixture(tmp_path / "a.json", "blue"), seed=3)
    second = load_locomo_arena_cases(_fixture(tmp_path / "b.json", "green"), seed=3)
    assert runtime_projection(first[0]) == runtime_projection(second[0])
    assert first[0].raw["gold_answer"] != second[0].raw["gold_answer"]
    assert first[0].subset == "single_hop"


def test_locomo_loader_validates_pool_budget(tmp_path: Path) -> None:
    path = _fixture(tmp_path / "data.json")
    try:
        load_locomo_arena_cases(path, seed=1, retrieval_top_k=5, candidate_pool_k=4)
    except ValueError as exc:
        assert "candidate_pool_k" in str(exc)
    else:
        raise AssertionError("invalid retrieval budget was accepted")
