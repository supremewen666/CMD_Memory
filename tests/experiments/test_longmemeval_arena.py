from __future__ import annotations

import copy
import json

import pytest

from experiments.longmemeval_arena import (
    load_longmemeval_arena_cases,
    runtime_projection,
)


def _row() -> dict[str, object]:
    return {
        "question_id": "q-1",
        "question_type": "single-session-user",
        "question": "Which degree did I graduate with?",
        "answer": "Master of Science in Robotics",
        "answer_session_ids": ["answer-session"],
        "haystack_dates": [
            "2024/01/01 (Mon) 09:00",
            "2024/01/02 (Tue) 09:00",
            "2024/01/03 (Wed) 09:00",
        ],
        "haystack_session_ids": ["distractor-1", "distractor-2", "answer-session"],
        "haystack_sessions": [
            [
                {"role": "user", "content": "I bought green tea."},
                {"role": "assistant", "content": "Enjoy your tea."},
            ],
            [
                {"role": "user", "content": "The weather is sunny."},
                {"role": "assistant", "content": "Have a nice day."},
            ],
            [
                {
                    "role": "user",
                    "content": "I graduated with a Master of Science in Robotics degree.",
                },
                {"role": "assistant", "content": "Congratulations!"},
            ],
        ],
    }


def _write(tmp_path, rows):
    path = tmp_path / "longmemeval.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def test_loader_retrieves_over_all_sessions_instead_of_first_two(tmp_path):
    case = load_longmemeval_arena_cases(
        _write(tmp_path, [_row()]),
        seed=7,
        retrieval_top_k=1,
        candidate_pool_k=2,
    )[0]

    baseline = case.raw["baseline_outputs"][0]
    assert baseline["retrieved_memory_ids"] == [
        "case:q-1:session:0002:answer-session"
    ]
    assert "Master of Science in Robotics" in case.base_context
    assert len(case.raw["extracted_memory"]) == 2
    assert case.raw["retrieval_protocol"] == {
        "strategy": "bm25",
        "history_sessions_scanned": 3,
        "retrieval_top_k": 1,
        "candidate_pool_k": 2,
        "answer_session_ids_used": False,
    }


def test_scorer_fields_cannot_change_runtime_projection(tmp_path):
    original = _row()
    changed = copy.deepcopy(original)
    changed["answer"] = "A deliberately different secret reference"
    changed["answer_session_ids"] = ["distractor-1"]

    case_a = load_longmemeval_arena_cases(
        _write(tmp_path, [original]),
        seed=7,
        retrieval_top_k=1,
        candidate_pool_k=2,
    )[0]
    case_b = load_longmemeval_arena_cases(
        _write(tmp_path, [changed]),
        seed=7,
        retrieval_top_k=1,
        candidate_pool_k=2,
    )[0]

    assert runtime_projection(case_a) == runtime_projection(case_b)
    assert case_a.raw["gold_answer"] != case_b.raw["gold_answer"]


def test_loader_rejects_candidate_pool_smaller_than_control(tmp_path):
    with pytest.raises(ValueError, match="candidate_pool_k"):
        load_longmemeval_arena_cases(
            _write(tmp_path, [_row()]),
            seed=7,
            retrieval_top_k=5,
            candidate_pool_k=4,
        )


def test_loader_normalizes_integer_reference_for_shadow_only(tmp_path):
    row = _row()
    row["answer"] = 3

    case = load_longmemeval_arena_cases(
        _write(tmp_path, [row]),
        seed=7,
        retrieval_top_k=1,
        candidate_pool_k=2,
    )[0]

    assert case.raw["gold_answer"] == "3"
    assert "gold_answer" not in runtime_projection(case)
