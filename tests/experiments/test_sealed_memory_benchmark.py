from __future__ import annotations

import json
from pathlib import Path

import pytest

from cmd_audit.eval.gold_free_observer import ProbeCoordinates
from experiments.arena_runner_common import ArenaCase, DualScoreExecution
from experiments.sealed_memory_benchmark import predict_and_seal, validate_seal
from experiments.official_memory_eval import score_locomo_official


class _Candidate:
    def __init__(self, skill_id: str) -> None:
        self.skill_id = skill_id


class _Backend:
    selection_judge_identity = "fake-selection"

    def candidates(self, case):
        return (_Candidate("worse"), _Candidate("better"))

    def evaluate(self, case, candidate, *, input_context, origin_context):
        gain = -0.2 if candidate.skill_id == "worse" else 0.5
        return DualScoreExecution(
            skill_id=candidate.skill_id,
            repaired_context=input_context + candidate.skill_id,
            gold_free_gain=gain,
            shadow_gold_gain=None,
            execution_cost=2.0,
            baseline_hypothesis="baseline",
            repaired_hypothesis=candidate.skill_id,
        )

    def cmd_call_counts(self, case):
        return (2, 2)

    def answer_context(self, case, context, *, purpose="benchmark_control"):
        return "full"


def _case(answer: str) -> ArenaCase:
    return ArenaCase(
        arena_id="locomo",
        case_id="s:q0000",
        family_id="locomo:single_hop",
        failure_type="unlabeled_observational",
        base_context="bm25 context",
        coordinates=ProbeCoordinates(question_type="single_hop"),
        subset="single_hop",
        raw={
            "query": "q",
            "raw_events": [],
            "extracted_memory": [],
            "baseline_outputs": [],
            "full_context": "all history",
            "gold_answer": answer,
        },
    )


def test_prediction_seal_is_gold_invariant_and_selects_positive_gain(tmp_path: Path) -> None:
    dataset = tmp_path / "data.json"
    dataset.write_text("[]", encoding="utf-8")
    first = predict_and_seal(
        benchmark="locomo", cases=[_case("gold-a")], backend=_Backend(),
        dataset_path=dataset, output=tmp_path / "run-a",
    )
    second = predict_and_seal(
        benchmark="locomo", cases=[_case("gold-b")], backend=_Backend(),
        dataset_path=dataset, output=tmp_path / "run-b",
    )
    assert first["runtime_stream_root"] == second["runtime_stream_root"]
    cmd = json.loads((tmp_path / "run-a/predictions/cmd.jsonl").read_text())
    assert cmd == {"hypothesis": "better", "question_id": "s:q0000"}
    assert validate_seal(tmp_path / "run-a")["sealed"] is True


def test_prediction_seal_rejects_posthoc_edit(tmp_path: Path) -> None:
    dataset = tmp_path / "data.json"
    dataset.write_text("[]", encoding="utf-8")
    run = tmp_path / "run"
    predict_and_seal(
        benchmark="locomo", cases=[_case("gold")], backend=_Backend(),
        dataset_path=dataset, output=run,
    )
    (run / "predictions/cmd.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed after seal"):
        validate_seal(run)


def test_locomo_official_adapter_scores_only_after_seal(tmp_path: Path) -> None:
    dataset = tmp_path / "locomo.json"
    dataset.write_text(json.dumps([{
        "sample_id": "s",
        "qa": [{"question": "q", "answer": "better", "category": 4, "evidence": []}],
    }]), encoding="utf-8")
    run = tmp_path / "run"
    predict_and_seal(
        benchmark="locomo", cases=[_case("better")], backend=_Backend(),
        dataset_path=dataset, output=run, include_full_context=False,
    )
    official = tmp_path / "official/task_eval"
    official.mkdir(parents=True)
    (official / "evaluation.py").write_text(
        "def eval_question_answering(rows, key):\n"
        "    scores = [1.0 if row[key] == str(row['answer']) else 0.0 for row in rows]\n"
        "    return scores, 0.0, []\n",
        encoding="utf-8",
    )
    report = score_locomo_official(
        run_dir=run, dataset=dataset, official_root=tmp_path / "official",
    )
    assert report["arms"]["cmd"]["official_f1"] == 1.0
    assert report["arms"]["bm25"]["official_f1"] == 0.0
    assert report["schema_version"] == "cmd-locomo-official-score-v2"
    assert report["arms"]["cmd"]["per_case"] == [{
        "question_id": "s:q0000", "category": 4, "official_f1": 1.0,
    }]
