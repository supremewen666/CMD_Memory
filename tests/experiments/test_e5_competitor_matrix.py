from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from experiments.e5_competitor_matrix import (
    COMPETITOR_RECORD_SCHEMA_VERSION,
    run_e5,
)


def _row(system_id: str, values: tuple[str, str, str, str]) -> dict[str, object]:
    return {
        "schema_version": COMPETITOR_RECORD_SCHEMA_VERSION,
        "system_id": system_id,
        "gold_free": values[0],
        "quality_fault_scope": values[1],
        "counterfactual_attribution_and_repair": values[2],
        "auditable_ledger": values[3],
        "source_ids": [f"source:{system_id}"],
        "notes": "curator supplied",
    }


def _write(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_e5_emits_source_bound_json_and_csv(tmp_path: Path) -> None:
    source = tmp_path / "competitors.jsonl"
    _write(
        source,
        [
            _row("CMD", ("yes", "yes", "yes", "yes")),
            _row("MemAudit", ("yes", "partial", "no", "partial")),
            _row("ERSkill", ("yes", "no", "partial", "no")),
        ],
    )
    output_json = tmp_path / "e5.json"
    output_csv = tmp_path / "e5.csv"
    report = run_e5(
        input_path=source,
        output_json=output_json,
        output_csv=output_csv,
    )
    assert report["competitor_count"] == 2
    assert report["project_memory_used_as_ground_truth"] is False
    assert report["model_calls"] == 0
    by_id = {row["competitor"]: row for row in report["comparisons"]}
    assert by_id["MemAudit"]["cmd_strictly_adds_dimensions"] == 3
    with output_csv.open(encoding="utf-8", newline="") as handle:
        assert {row["competitor"] for row in csv.DictReader(handle)} == {
            "MemAudit",
            "ERSkill",
        }
    with pytest.raises(ValueError, match="overwrite"):
        run_e5(
            input_path=source,
            output_json=output_json,
            output_csv=tmp_path / "another.csv",
        )


def test_e5_fails_closed_on_uncited_or_open_rows(tmp_path: Path) -> None:
    source = tmp_path / "bad.jsonl"
    bad = _row("CMD", ("yes", "yes", "yes", "yes"))
    competitor = _row("Other", ("yes", "partial", "no", "no"))
    competitor["source_ids"] = []
    _write(source, [bad, competitor])
    with pytest.raises(ValueError, match="source IDs"):
        run_e5(
            input_path=source,
            output_json=tmp_path / "e5.json",
            output_csv=tmp_path / "e5.csv",
        )
