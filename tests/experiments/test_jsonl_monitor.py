from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments.jsonl_monitor import read_jsonl, snapshot


def test_monitor_combines_lifecycle_and_progress_streams(tmp_path: Path) -> None:
    status = tmp_path / "status.jsonl"
    progress = tmp_path / "progress.jsonl"
    status.write_text(
        json.dumps({"event": "running", "role": "v4_gpu0", "gpu_id": "0"})
        + "\n",
        encoding="utf-8",
    )
    progress.write_text(
        json.dumps({"event": "case_completed", "completed": 3, "total": 10})
        + "\n",
        encoding="utf-8",
    )

    rows = snapshot((status, progress))
    assert [row["event"] for row in rows] == ["running", "case_completed"]
    assert rows[1]["progress_fraction"] == 0.3


def test_monitor_rejects_non_mapping_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text("[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        read_jsonl(path)
