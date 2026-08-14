from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments.v4_materialization import (
    lane_for_case,
    materialize_shard,
    merge_materialized_shards,
)


def _raw(case_id: str, event_index: int) -> dict[str, object]:
    return {
        "case_id": case_id,
        "context": {"event_index": event_index},
        "payload": f"payload-{case_id}",
    }


def test_gpu_lanes_are_disjoint_and_merge_restores_frozen_order(tmp_path: Path) -> None:
    raw = tuple(_raw(f"case-{index}", 9 - index) for index in range(8))

    def backend(row, lane):
        return {
            "case_id": row["case_id"],
            "event_index": row["context"]["event_index"],
            "lane": lane,
            "materialized": row["payload"],
        }

    shards = []
    for lane in ("gpu0", "gpu1"):
        output = tmp_path / f"{lane}.jsonl"
        progress = tmp_path / f"{lane}.progress.jsonl"
        result = materialize_shard(
            raw,
            lane=lane,
            output=output,
            progress=progress,
            backend=backend,
            validator=lambda row: row,
            model_call_accounting={"answer_generation": 3, "shadow_judge": 3},
        )
        shards.append(output)
        assert result["case_ids"] == sorted(
            row["case_id"] for row in raw if lane_for_case(row["case_id"]) == lane
        )
        events = [
            json.loads(line)["event"]
            for line in progress.read_text(encoding="utf-8").splitlines()
        ]
        assert events[0] == "started"
        assert events[-1] == "completed"

    merged = tmp_path / "merged.jsonl"
    manifest = merge_materialized_shards(
        shards,
        output=merged,
        expected_case_ids={row["case_id"] for row in raw},
        validator=lambda row: row,
        event_index=lambda row: row["event_index"],
    )
    rows = [json.loads(line) for line in merged.read_text(encoding="utf-8").splitlines()]
    assert [row["event_index"] for row in rows] == sorted(
        row["context"]["event_index"] for row in raw
    )
    assert manifest["case_count"] == len(raw)
    assert set(manifest["shard_sha256"]) == {str(path.resolve()) for path in shards}
    assert manifest["materialization_model_calls"] == 12
    assert manifest["materialization_model_call_accounting"] == {
        "answer_generation": 6,
        "shadow_judge": 6,
    }


def test_merge_rejects_duplicate_or_missing_cases(tmp_path: Path) -> None:
    row = {"case_id": "duplicate", "event_index": 1}
    shards = []
    for name in ("a", "b"):
        path = tmp_path / f"{name}.jsonl"
        path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        shards.append(path)
    with pytest.raises(ValueError, match="duplicate case_id"):
        merge_materialized_shards(
            shards,
            output=tmp_path / "merged.jsonl",
            expected_case_ids={"duplicate", "missing"},
            validator=lambda value: value,
            event_index=lambda value: value["event_index"],
        )


def test_single_gpu_lane_materializes_and_merges_every_case(tmp_path: Path) -> None:
    raw = tuple(_raw(f"case-{index}", 5 - index) for index in range(5))
    shard = tmp_path / "single_gpu.jsonl"
    result = materialize_shard(
        raw,
        lane="single_gpu",
        output=shard,
        progress=tmp_path / "progress.jsonl",
        backend=lambda row, lane: {
            "case_id": row["case_id"],
            "event_index": row["context"]["event_index"],
            "lane": lane,
        },
        validator=lambda row: row,
        model_call_accounting={"answer_generation": 5, "shadow_judge": 5},
    )
    assert result["partition_rule"] == "all_cases"
    assert result["case_ids"] == sorted(row["case_id"] for row in raw)

    merged = tmp_path / "merged.jsonl"
    manifest = merge_materialized_shards(
        (shard,),
        output=merged,
        expected_case_ids={row["case_id"] for row in raw},
        validator=lambda row: row,
        event_index=lambda row: row["event_index"],
    )
    rows = [json.loads(line) for line in merged.read_text(encoding="utf-8").splitlines()]
    assert [row["event_index"] for row in rows] == [1, 2, 3, 4, 5]
    assert manifest["materialization_mode"] == "single_gpu"
    assert manifest["shard_count"] == 1
    assert manifest["materialization_model_calls"] == 10


def test_single_gpu_merge_rejects_one_dual_gpu_half_shard(tmp_path: Path) -> None:
    raw = tuple(_raw(f"case-{index}", index) for index in range(5))
    shard = tmp_path / "gpu0.jsonl"
    materialize_shard(
        raw,
        lane="gpu0",
        output=shard,
        progress=tmp_path / "progress.jsonl",
        backend=lambda row, _lane: {
            "case_id": row["case_id"],
            "event_index": row["context"]["event_index"],
        },
        validator=lambda row: row,
    )
    with pytest.raises(ValueError, match="single_gpu all_cases"):
        merge_materialized_shards(
            (shard,),
            output=tmp_path / "merged.jsonl",
            expected_case_ids=None,
            validator=lambda row: row,
            event_index=lambda row: row["event_index"],
        )
