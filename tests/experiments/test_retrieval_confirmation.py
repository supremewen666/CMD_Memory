from __future__ import annotations

from pathlib import Path
import pytest

from cmd_audit.core.state_codec import content_sha256
import math
from collections import Counter

from experiments.baselines.retrieval_confirmation import BM25Strategy, LexicalStrategy, _records, _tokens, run
from experiments.run_longmemeval_m0_r1 import _canonical_session, _case_stream

def test_bm25_formula_ranking_and_stable_tie() -> None:
    records = _records(("apple apple", "apple banana", "pear"), "scope")
    bm25=BM25Strategy(); bm25.index(records)
    assert [r.memory_id for r in bm25.search("apple", 2)] == [records[0].memory_id, records[1].memory_id]
    lexical=LexicalStrategy(); lexical.index(_records(("zero", "none"), "scope"))
    assert [r.memory_id for r in lexical.search("absent", 2)] == ["m-0-" + content_sha256("zero")[:12], "m-1-" + content_sha256("none")[:12]]

def test_bm25_optimized_path_matches_preoptimization_formula_on_s_cases() -> None:
    """Real S sample: every rank equals the former direct formula, not just metrics."""
    for case in _case_stream(Path("data/external/longmemeval/input/longmemeval_s_cleaned.json"), 3):
        records=_records([_canonical_session(s) for _,_,s in case["ordered"]], "s")
        tfs=[Counter(_tokens(r.content)) for r in records]; lengths=[sum(tf.values()) for tf in tfs]; avgdl=sum(lengths)/len(lengths); df=Counter(term for tf in tfs for term in tf); terms=_tokens(str(case["question"]))
        def old_score(i: int) -> float:
            return sum(math.log(1+(len(records)-df[t]+.5)/(df[t]+.5))*tfs[i][t]*2.2/(tfs[i][t]+1.2*(.25+.75*lengths[i]/avgdl)) for t in set(terms) if tfs[i][t])
        old=[r.memory_id for _,r in sorted(((old_score(i),r) for i,r in enumerate(records)),key=lambda x:(-x[0],x[1].memory_id))[:5]]
        current=BM25Strategy(); current.index(records)
        assert [r.memory_id for r in current.search(str(case["question"]),5)] == old

def test_budget_and_oracle_isolation(tmp_path: Path) -> None:
    output=tmp_path/"ceiling"
    m=run(dataset="memfail",strategy="oracle-ceiling",output=output,limit=1,top_k=1)
    assert m["offline_upper_bound"] is True and m["prediction_context"] is False
    assert not (output/"rows.jsonl").exists()
    assert m["summary"]["retention"] == 1.0

def test_fresh_resume_and_manifest(tmp_path: Path) -> None:
    out=tmp_path/"run"; first=run(dataset="longmemeval-s",strategy="bm25",output=out,limit=1,top_k=2)
    assert first["strategy"] == "bm25" and first["gold_firewall"]["scorer_after_retrieval"]
    assert run(dataset="longmemeval-s",strategy="bm25",output=out,limit=1,top_k=2,run_mode="resume") == first
    with pytest.raises(ValueError, match="manifest mismatch"):
        run(dataset="longmemeval-s",strategy="lexical",output=out,limit=1,top_k=2,run_mode="resume")

def test_crash_style_resume_uses_durable_gold_free_rankings(tmp_path: Path) -> None:
    out=tmp_path/"crash"; first=run(dataset="longmemeval-s",strategy="bm25",output=out,limit=1,top_k=2)
    (out/"manifest.json").unlink(); (out/"rows.jsonl").unlink()
    resumed=run(dataset="longmemeval-s",strategy="bm25",output=out,limit=1,top_k=2,run_mode="resume")
    assert resumed["summary"] == first["summary"]
    assert (out/"rankings.jsonl").read_text().count("\n") == 1

def test_minilm_unavailable_is_explicit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import experiments.baselines.retrieval_confirmation as baseline
    monkeypatch.setattr(baseline, "_strategy", lambda _: (_ for _ in ()).throw(RuntimeError("unavailable: all-MiniLM-L6-v2 weights are not available locally")))
    result=baseline.run(dataset="longmemeval-s",strategy="minilm",output=tmp_path/"m",limit=1)
    assert result["status"] == "unavailable" and "weights" in result["reason"]
