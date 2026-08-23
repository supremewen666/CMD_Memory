"""Build closed, deployment-visible P4B typed-evidence ledgers from P4A BM25.

This is deliberately not an outcome labeler.  A ranking/cache can establish
candidate parity, but cannot by itself establish selected-action typed outcome
coverage; the emitted machine gate stays blocked until such evidence exists.
"""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
from typing import Any, Sequence

from cmd_audit.core.state_codec import append_jsonl_fsync, atomic_json_write, content_sha256
from experiments.run_longmemeval_m0_r1 import _canonical_session, _case_stream, _file_sha256, _safe_instance_name

SCHEMA="cmd-p4b-typed-evidence-v1"
FEATURE_SCHEMA="cmd-p4b-visible-features-v1"
FORBIDDEN=frozenset({"answer","oracle","gold","question_type","family","subtype","label","recovery"})
ALLOWED=frozenset({"query_sha256","candidate_sha256","candidate_rank","candidate_length","source_date","source_session_sha256","redundancy","diversity","trace_cost","score_available","score_gap_available"})

def _sha(path: Path)->str: return hashlib.sha256(path.read_bytes()).hexdigest()
def _assert_visible(value: object) -> None:
    if isinstance(value, dict):
        for key,item in value.items():
            if any(token in str(key).lower() for token in FORBIDDEN): raise ValueError(f"forbidden P4B runtime field: {key}")
            _assert_visible(item)
    elif isinstance(value,list):
        for item in value: _assert_visible(item)

def _gate(rows: list[dict[str,Any]], binding: dict[str,Any]) -> dict[str,Any]:
    # Files only prove cache closure.  Promotion needs selected, delayed typed
    # outcome pairs and a decoupling audit; neither is manufactured here.
    return {"schema_version":"cmd-p4b-typed-evidence-gate-v1","decision":"BLOCKED_TYPED_EVIDENCE_UNAVAILABLE","cache_closed":bool(rows),"selected_action_typed_outcomes":0,"pairwise_comparable_coverage":0.0,"required_pairwise_coverage":0.50,"decoupling_controls":"NOT_RUN_EVIDENCE_UNAVAILABLE","promotion_allowed":False,"binding_root":content_sha256(binding,ensure_ascii=False,allow_nan=False)}

def build(*, dataset:str, ranking_root:Path, output:Path, run_mode:str="fresh", limit:int=0)->dict[str,Any]:
    if dataset not in {"longmemeval-s","longmemeval-m","memfail"}: raise ValueError("unsupported dataset")
    if run_mode=="fresh" and output.exists() and any(output.iterdir()): raise ValueError("fresh refuses nonempty output")
    manifest_path=output/"manifest.json"
    if run_mode=="resume":
        if not manifest_path.exists(): raise ValueError("resume requires manifest")
        prior=json.loads(manifest_path.read_text());
        if prior.get("ranking_root")!=str(ranking_root.resolve()): raise ValueError("resume ranking root mismatch")
        return prior
    ranking=ranking_root/"rankings.jsonl"; receipt=ranking_root/"run_receipt.json"; p4a=ranking_root/"manifest.json"
    binding={"dataset":dataset,"ranking_root":str(ranking_root.resolve()),"ranking_sha256":_sha(ranking) if ranking.is_file() else None,"receipt_sha256":_sha(receipt) if receipt.is_file() else None,"p4a_manifest_sha256":_sha(p4a) if p4a.is_file() else None,"feature_schema":FEATURE_SCHEMA,"feature_allowlist":sorted(ALLOWED)}
    if not ranking.is_file() or not receipt.is_file():
        result={"schema_version":SCHEMA,**binding,"status":"BLOCKED_CANDIDATE_CACHE_UNAVAILABLE","paper_role":"legacy","mainline":False,"scientific_status":"degraded_artifact_dependent_baseline","reason":"The legacy P4A ranking/cache receipt is unavailable; it is not reconstructed because that would manufacture new evidence for a degraded path.","typed_evidence_gate":_gate([],binding),"network":"prohibited","api":"prohibited","mem0":"prohibited"}; output.mkdir(parents=True,exist_ok=True); atomic_json_write(manifest_path,result,ensure_ascii=False,allow_nan=False,indent=2,trailing_newline=True); return result
    root=Path(__file__).resolve().parents[1]; data=root/"data/external/longmemeval/input"/("longmemeval_s_cleaned.json" if dataset.endswith("s") else "longmemeval_m_cleaned.json")
    cases={str(c["question_id"]):c for c in _case_stream(data,limit or 10**9)}; rows=[]; output.mkdir(parents=True,exist_ok=True); ledger=output/"typed_evidence.jsonl"
    for ordinal,line in enumerate(ranking.read_text(encoding="utf-8").splitlines(),1):
        if limit and ordinal > limit: break
        rank=json.loads(line); case=cases.get(str(rank.get("case_id")))
        if case is None: raise ValueError("ranking case is not in frozen input")
        visible={content_sha256(_canonical_session(session)): (date,sid,_canonical_session(session)) for date,sid,session in case["ordered"]}
        candidate_rows=[]
        for position,source in enumerate(rank.get("found_source_hashes",()),1):
            if source not in visible: raise ValueError("candidate hash not in frozen visible history")
            date,sid,text=visible[source]; tokens=set(text.lower().split()); previous=[set(x["_text"].lower().split()) for x in candidate_rows]
            redundancy=max((len(tokens&p)/max(1,len(tokens|p)) for p in previous),default=0.0)
            candidate_rows.append({"candidate_sha256":source,"candidate_rank":position,"candidate_length":len(text),"source_date":date,"source_session_sha256":content_sha256(sid),"redundancy":redundancy,"diversity":1.0-redundancy,"trace_cost":position,"score_available":False,"score_gap_available":False,"_text":text})
        features={"query_sha256":content_sha256(str(case["question"])),"candidates":[{k:v for k,v in x.items() if k!="_text"} for x in candidate_rows]}
        _assert_visible(features)
        if set(features) != {"query_sha256","candidates"} or any(set(x)-ALLOWED for x in features["candidates"]): raise ValueError("feature allowlist violation")
        row={"schema_version":SCHEMA,"event_index":ordinal,"case_id_sha256":content_sha256(str(case["question_id"])),"candidate_root":content_sha256(features["candidates"],ensure_ascii=False,allow_nan=False),"features":features,"actionability":"not_actionable","reason":"ranking has no selected-action typed outcome telemetry","previous_hash":rows[-1]["event_hash"] if rows else "0"*64}
        row["event_hash"]=content_sha256(row,ensure_ascii=False,allow_nan=False); append_jsonl_fsync(ledger,row,ensure_ascii=False,allow_nan=False); rows.append(row)
    result={"schema_version":SCHEMA,**binding,"status":"success","paper_role":"legacy","mainline":False,"scientific_status":"degraded_not_for_primary_claim","case_count":len(rows),"ledger_sha256":_sha(ledger),"ledger_head":rows[-1]["event_hash"] if rows else "0"*64,"typed_evidence_gate":_gate(rows,binding),"runtime_forbidden":sorted(FORBIDDEN),"label_sidecar":"not opened by builder","network":"prohibited","api":"prohibited","mem0":"prohibited"}
    atomic_json_write(manifest_path,result,ensure_ascii=False,allow_nan=False,indent=2,trailing_newline=True); return result

def main(argv:Sequence[str]|None=None)->int:
    p=argparse.ArgumentParser(); p.add_argument("--dataset",required=True); p.add_argument("--ranking-root",type=Path,required=True); p.add_argument("--output",type=Path,required=True); p.add_argument("--run-mode",choices=("fresh","resume"),default="fresh"); p.add_argument("--limit",type=int,default=0); a=p.parse_args(argv); build(dataset=a.dataset,ranking_root=a.ranking_root,output=a.output,run_mode=a.run_mode,limit=a.limit); return 0
if __name__=="__main__": raise SystemExit(main())
