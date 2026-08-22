from __future__ import annotations
import json
from pathlib import Path
import pytest
from experiments.build_p4b_typed_evidence import build
from experiments.run_p4b_cmd_bm25 import run

ROOT=Path("artifacts/experiments/p4a_baseline_confirmation")
def test_p4b_gate_requires_outcomes_and_abstains(tmp_path:Path):
    evidence=tmp_path/"e"; m=build(dataset="longmemeval-s",ranking_root=ROOT/"longmemeval_s_bm25_optimized",output=evidence,limit=1)
    assert m["typed_evidence_gate"]["promotion_allowed"] is False
    r=run(evidence=evidence,output=tmp_path/"r")
    assert r["status"] == "BLOCKED_TYPED_EVIDENCE_UNAVAILABLE" and r["candidate_parity"]
    assert {x["selection"] for x in map(json.loads,(tmp_path/"r"/"decisions.jsonl").read_text().splitlines())} == {"baseline","abstain"}
def test_memfail_missing_cache_is_explicit(tmp_path:Path):
    m=build(dataset="memfail",ranking_root=ROOT/"memfail_bm25",output=tmp_path/"m")
    assert m["status"] == "BLOCKED_CANDIDATE_CACHE_UNAVAILABLE"
def test_resume_and_tamper_receipt(tmp_path:Path):
    out=tmp_path/"e"; build(dataset="longmemeval-s",ranking_root=ROOT/"longmemeval_s_bm25_optimized",output=out,limit=1)
    assert build(dataset="longmemeval-s",ranking_root=ROOT/"longmemeval_s_bm25_optimized",output=out,run_mode="resume")["status"]=="success"
    with pytest.raises(ValueError): build(dataset="longmemeval-m",ranking_root=ROOT/"longmemeval_m_bm25_full",output=out,run_mode="resume")
