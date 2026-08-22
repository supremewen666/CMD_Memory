"""P4B CMD/static/GHOST selection over frozen BM25 candidate evidence.

No arm may retrieve, enlarge a candidate budget, or read labels.  Until the
typed-evidence gate has selected-action coverage, CMD and GHOST fail closed to
abstention and no efficacy metric is emitted.
"""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
from typing import Any, Sequence
from cmd_audit.core.state_codec import append_jsonl_fsync, atomic_json_write, content_sha256

ARMS=("bm25","static","cmd","ghost")
def _sha(path:Path)->str:return hashlib.sha256(path.read_bytes()).hexdigest()
def run(*, evidence:Path, output:Path, run_mode:str="fresh", context_budget:int=5)->dict[str,Any]:
    if context_budget<1: raise ValueError("positive context budget required")
    if run_mode=="fresh" and output.exists() and any(output.iterdir()): raise ValueError("fresh refuses nonempty output")
    manifest_path=output/"manifest.json"
    if run_mode=="resume":
        if not manifest_path.exists(): raise ValueError("resume requires manifest")
        return json.loads(manifest_path.read_text())
    source=json.loads((evidence/"manifest.json").read_text()); gate=source.get("typed_evidence_gate",{})
    if source.get("status")!="success": raise ValueError("typed evidence is unavailable")
    ledger=evidence/"typed_evidence.jsonl"; rows=[json.loads(x) for x in ledger.read_text().splitlines()]
    output.mkdir(parents=True,exist_ok=True); decisions=output/"decisions.jsonl"; active=bool(gate.get("promotion_allowed"))
    # An active path is intentionally unavailable until a registered typed
    # incident/operator evidence source is supplied; no synthetic policy exists.
    for row in rows:
        candidates=row["features"]["candidates"][:context_budget]; root=content_sha256(candidates,ensure_ascii=False,allow_nan=False)
        for arm in ARMS:
            decision={"schema_version":"cmd-p4b-frozen-bm25-decision-v1","case_id_sha256":row["case_id_sha256"],"arm":arm,"candidate_root":root,"candidate_count":len(candidates),"context_budget":context_budget,"selection":"baseline" if arm=="bm25" else "abstain","active":False,"reason":"typed evidence gate blocked; no legal selected-action repair evidence" if arm!="bm25" else "frozen BM25 baseline","evidence_event_hash":row["event_hash"]}
            append_jsonl_fsync(decisions,decision,ensure_ascii=False,allow_nan=False)
    result={"schema_version":"cmd-p4b-frozen-bm25-run-v1","status":"BLOCKED_TYPED_EVIDENCE_UNAVAILABLE" if not active else "BLOCKED_OPERATOR_ABI_UNAVAILABLE","evidence_root":str(evidence.resolve()),"evidence_manifest_sha256":_sha(evidence/"manifest.json"),"evidence_ledger_sha256":_sha(ledger),"decision_sha256":_sha(decisions),"arms":list(ARMS),"case_count":len(rows),"candidate_parity":True,"top_k":context_budget,"context_budget":context_budget,"active_repairs":0,"label_sidecar":"UNAVAILABLE_NOT_OPENED","headline_metrics":"UNAVAILABLE","gates":{"typed_evidence":gate,"root_parity":True,"overall_noninferiority":"BLOCKED","knowledge_update":"UNAVAILABLE","temporal_confusion":"UNAVAILABLE","memfail_process_fault":"UNAVAILABLE","coexisting_control":"UNAVAILABLE","false_repair":"UNAVAILABLE"},"network":"prohibited","api":"prohibited","mem0":"prohibited"}
    atomic_json_write(manifest_path,result,ensure_ascii=False,allow_nan=False,indent=2,trailing_newline=True); return result
def main(argv:Sequence[str]|None=None)->int:
    p=argparse.ArgumentParser();p.add_argument("--evidence",type=Path,required=True);p.add_argument("--output",type=Path,required=True);p.add_argument("--run-mode",choices=("fresh","resume"),default="fresh");p.add_argument("--context-budget",type=int,default=5);a=p.parse_args(argv);run(evidence=a.evidence,output=a.output,run_mode=a.run_mode,context_budget=a.context_budget);return 0
if __name__=="__main__":raise SystemExit(main())
