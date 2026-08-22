from __future__ import annotations

import json
from pathlib import Path

import pytest

from cmd_audit.adapters.memfail import MemFailSchemaError
from experiments.memory_data_plane import Mem0DataPlane
from experiments.run_memfail_m0_r1 import _read_rows, _visible_cases, run


DATA = Path("data/external/memfail/datasets")


def test_official_flattening_counts_and_per_family_limit():
    rows = _read_rows(DATA)
    assert sum(map(len, rows.values())) == 492
    assert len(_visible_cases(rows, 0)) == 692
    # One physical row from each family; the first persona row has four prompts.
    assert len(_visible_cases(rows, 1)) == 7


def test_label_firewall_scope_isolation_and_resume(tmp_path):
    output = tmp_path / "run"
    first = run(DATA, output, arms=("vanilla", "static", "cmd", "ghost"), limit=1, top_k=2)
    again = run(DATA, output, arms=("vanilla", "static", "cmd", "ghost"), limit=1, top_k=2, resume=True)
    assert first["outcome_root"] == again["outcome_root"]
    assert first["scored_prompts"] == 7
    audit = "".join(p.read_text() for p in (output / "audits").glob("*.jsonl"))
    assert "ground_truth" not in audit and "correct_choice" not in audit and "is_misleading" not in audit
    rows = [json.loads(line) for line in (output / "outcomes.jsonl").read_text().splitlines()]
    scopes = [r["scope_root"] for event in rows for r in event["rows"]]
    assert len(scopes) == len(set(scopes))


def test_schema_fail_closed_and_no_mem0_implicit_config(tmp_path):
    rows = _read_rows(DATA)
    rows["persona"][0]["questions"] = "not-json"
    with pytest.raises(MemFailSchemaError):
        _visible_cases(rows, 1)
    with pytest.raises(ValueError, match="mem0-config"):
        run(DATA, tmp_path / "out", backend="mem0", limit=1)


def test_incident_only_after_scoring_and_conflict_is_control(tmp_path):
    output = tmp_path / "run"
    run(DATA, output, arms=("vanilla",), limit=1, top_k=1)
    incidents = [json.loads(line) for line in (output / "incidents.jsonl").read_text().splitlines()]
    assert incidents  # long-hop/conditional misses are scorer-confirmed
    assert all(row["mechanism"] == "process_fault" for row in incidents)
    assert all("coexisting" not in row["provenance"]["case_id"] for row in incidents)
    outcome = (output / "outcomes.jsonl").read_text()
    assert outcome  # outcome journal exists before durable checkpoint resume can occur


def test_mem0_fake_client_boundary(tmp_path):
    class Fake:
        def __init__(self): self.calls=[]
        def add(self, content, *, user_id): self.calls.append(("add", user_id)); return {}
        def search(self, query, *, user_id, limit): self.calls.append(("search", user_id, limit)); return {"results": [{"id":"x", "memory":"value"}]}
    fake = Fake()
    def factory(scope, audit): return Mem0DataPlane(namespace=scope, user_id=scope, config={"vector_store": {}}, audit_path=audit, client=fake)
    manifest = run(DATA, tmp_path / "run", backend="mem0", plane_factory=factory, arms=("vanilla",), limit=1)
    assert manifest["scored_prompts"] == 7
    assert {call[1] for call in fake.calls}
