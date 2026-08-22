"""Offline MemFail process-fault M0/R1 execution runner.

The data-plane receives only deployment-visible query, content and a unique
scope.  Benchmark labels (including choices, answers and misleading flags) are
opened by ``_score_case`` only after every arm completed add/search.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from cmd_audit.adapters.memfail import MEMFAIL_CSV_FILENAMES, MEMFAIL_TASKS, MemFailSchemaError, memfail_record_to_probe_cases
from cmd_audit.core.state_codec import atomic_json_write, content_sha256
from cmd_audit.repair.incident_store import IncidentLedger
from cmd_audit.repair.incident_triage import IncidentMechanism, ProcessFaultSubtype, RepairFamily, TriageDecision
from experiments.memory_data_plane import AuditedInMemoryDataPlane, Mem0DataPlane, MemoryRecord
from experiments.v4_run_checkpoint import OutcomeJournal, RunCheckpoint, RunCheckpointStore

VALID_ARMS = ("vanilla", "static", "cmd", "ghost")
SUBDIRS = {"long_hop": "long_hop", "coexisting": "coexisting_facts", "conditional_easy": "conditional_facts/easy", "conditional_hard": "conditional_facts/hard", "persona": "custom_persona_retrieval"}
REQUIRED = {
    "long_hop": {"id", "hop_count", "fact_1", "graded_question", "ground_truth_answer", "correct_choice"},
    "coexisting": {"preference_category", "preferences", "preference_facts", "question", "ground_truth_answer"},
    "conditional_easy": {"entity", "behavior", "condition", "entity_facts", "question", "condition_met", "ground_truth_answer"},
    "conditional_hard": {"entity", "behavior", "condition", "entity_facts", "question", "condition_met", "ground_truth_answer"},
    "persona": {"entity", "entity_facts", "questions"},
}

@dataclass(frozen=True)
class VisibleCase:
    position: int; case_id: str; task: str; source_row: int; query_index: int
    query: str; content: tuple[str, ...]; row: Mapping[str, str]

def _csv_path(root: Path, task: str) -> Path:
    path = root / SUBDIRS[task] / MEMFAIL_CSV_FILENAMES[task]
    if not path.is_file(): raise MemFailSchemaError(f"missing official MemFail CSV: {path}")
    return path

def _read_rows(root: Path) -> dict[str, list[dict[str, str]]]:
    out = {}
    for task in MEMFAIL_TASKS:
        with _csv_path(root, task).open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None or not REQUIRED[task].issubset(reader.fieldnames):
                raise MemFailSchemaError(f"{task}: official schema is malformed or incomplete")
            rows = list(reader)
        if not rows: raise MemFailSchemaError(f"{task}: empty CSV")
        out[task] = rows
    if sum(map(len, out.values())) != 492: raise MemFailSchemaError("expected 492 MemFail physical rows")
    return out

def _plain_question(value: str) -> str: return value.split("\n\nOptions:", 1)[0].strip()
def _json_list(value: str, label: str) -> list[Any]:
    try: result=json.loads(value)
    except json.JSONDecodeError as exc: raise MemFailSchemaError(f"malformed {label} JSON") from exc
    if not isinstance(result, list): raise MemFailSchemaError(f"{label} must be a JSON list")
    return result
def _groups(value: str) -> list[str]:
    # Sentence grouping is deployment-visible profile content, never labels.
    return [x.strip() for x in value.replace("!", ".").replace("?", ".").split(".") if x.strip()] or [value]

def _visible_cases(rows: Mapping[str, list[dict[str, str]]], limit: int) -> list[VisibleCase]:
    cases=[]
    for task in MEMFAIL_TASKS:
        selected=rows[task][:limit] if limit else rows[task]  # --limit is explicitly per family.
        for source_row,row in enumerate(selected):
            if task == "long_hop":
                content=tuple(row[k].strip() for k in ("fact_1","fact_2","fact_3","fact_4") if row.get(k," ").strip()); query=_plain_question(row["graded_question"]); count=1
            elif task == "coexisting": content=tuple(str(x) for x in _json_list(row["preference_facts"], "preference_facts")); query=row["question"].strip(); count=1
            elif task.startswith("conditional"):
                content=tuple(_groups(" ".join(str(x) for x in _json_list(row["entity_facts"], "entity_facts"))) + [f"{row['entity']} {row['behavior']} {row['condition']}."]); query=row["question"].strip(); count=1
            else:
                content=tuple(_groups(" ".join(str(x) for x in _json_list(row["entity_facts"], "entity_facts")))); questions=_json_list(row["questions"], "questions"); count=len(questions)
            if not content or not query if task != "persona" else not content: raise MemFailSchemaError(f"{task} row {source_row}: no visible content")
            for q in range(count):
                qtext = query if task != "persona" else str(questions[q].get("text", "")).strip()
                if not qtext: raise MemFailSchemaError(f"persona row {source_row}: question without text")
                cases.append(VisibleCase(len(cases)+1, f"memfail-{task}-{source_row:04d}-q{q}", task, source_row, q, qtext, content, row))
    if not limit and len(cases) != 692: raise MemFailSchemaError("expected 692 scored prompts after persona expansion")
    return cases

def _safe(value: str) -> str: return hashlib.sha256(value.encode()).hexdigest()[:20]
def _file_hash(path: Path) -> str:
    h=hashlib.sha256();
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1<<20), b""): h.update(chunk)
    return h.hexdigest()

def _score_case(case: VisibleCase, found: Sequence[MemoryRecord]) -> dict[str, Any]:
    """Offline-only scorer.  This is deliberately called after all plane I/O."""
    mapped=memfail_record_to_probe_cases(dict(case.row), task=case.task, row_index=case.source_row)[case.query_index]
    gold={content_sha256(item.text, ensure_ascii=False, allow_nan=False) for item in mapped.gold_evidence}
    relevant=[r for r in found if r.source_hash in gold]
    rank=next((i for i,r in enumerate(found,1) if r.source_hash in gold), None)
    family = "conditional" if case.task.startswith("conditional") else case.task
    subtype = ("retrieval" if case.task == "long_hop" else "granularity" if case.task.startswith("conditional") else "safety" if case.task == "persona" else "conflict_control")
    # Offline strata only; none of these values crossed the data plane.
    return {"family":family,"subtype":subtype,"scorable":bool(gold),"recall":bool(relevant) if gold else None,"mrr":(1/rank if rank else 0.0) if gold else None,
            "hop_count":str(case.row.get("hop_count","")) if case.task=="long_hop" else None,
            "condition_met":str(case.row.get("condition_met","")) if case.task.startswith("conditional") else None,
            "persona_kind":("misleading" if bool(_json_list(case.row["questions"],"questions")[case.query_index].get("is_misleading")) else "nonmisleading") if case.task=="persona" else None,
            "unsafe_answer":"unavailable" if case.task=="persona" else None}

def run(data_root: Path, output: Path, *, backend="in-memory", arms: Sequence[str]=VALID_ARMS, limit=0, resume=False, top_k=5, mem0_config: Mapping[str,object]|None=None, plane_factory: Any|None=None) -> dict[str,object]:
    if limit<0 or top_k<1: raise ValueError("limit must be nonnegative and top-k positive")
    if backend == "mem0" and not mem0_config and plane_factory is None: raise ValueError("--backend mem0 requires --mem0-config")
    arms=tuple(arms)
    if not arms or len(set(arms))!=len(arms) or any(a not in VALID_ARMS for a in arms): raise ValueError("invalid arms")
    rows=_read_rows(Path(data_root)); cases=_visible_cases(rows,limit); output=Path(output)
    roots={task:_file_hash(_csv_path(Path(data_root),task)) for task in MEMFAIL_TASKS}; meta=Path(data_root)/"long_hop"/"long_hop_chains_meta.json"
    if not meta.is_file(): raise MemFailSchemaError("missing long-hop metadata")
    binding={"data_roots":roots,"metadata_root":_file_hash(meta),"case_root":content_sha256([c.case_id for c in cases]),"backend":backend,"arms":arms,"top_k":top_k,"limit_per_family":limit}
    manifest_root=content_sha256(binding,ensure_ascii=False,allow_nan=False); journal=OutcomeJournal(output/"outcomes.jsonl"); checkpoints=RunCheckpointStore(output/"checkpoint")
    if resume: start=checkpoints.load_latest(manifest_sha256=manifest_root,case_stream_sha256=binding["case_root"]).next_position
    else:
        if output.exists() and any(output.iterdir()): raise ValueError("fresh refuses a non-empty output directory")
        start=0
    (output/"audits").mkdir(parents=True,exist_ok=True); ledger=IncidentLedger(output/"incidents.jsonl")
    for case in cases[start:]:
        pending=[]; started=time.perf_counter()
        for arm in arms:
            # The scope contains only an opaque ordinal, never task/family labels.
            scope=f"memfail:{arm}:{_safe(f'case:{case.position}')}"; pending_path=output/"audits"/f"{_safe(case.case_id)}.{arm}.jsonl.pending"; final=pending_path.with_suffix(""); pending_path.unlink(missing_ok=True)
            plane=(plane_factory(scope, pending_path) if plane_factory else (AuditedInMemoryDataPlane(pending_path,sync_each_event=False) if backend=="in-memory" else Mem0DataPlane(namespace=scope,user_id=scope,config=mem0_config or {},audit_path=pending_path)))
            for content in case.content: plane.add(content=content,scope=scope)
            found=plane.search(query=case.query,scope=scope,limit=top_k); plane.flush()
            if pending_path.exists(): os.replace(pending_path,final)
            pending.append((arm,scope,plane,found))
        elapsed=(time.perf_counter()-started)*1000
        result=[]
        for arm,scope,plane,found in pending:
            score=_score_case(case,found); row={"arm":arm,"case_id":case.case_id,"scope_root":content_sha256(scope),"memory_root":plane.root(scope),"audit_head":plane.audit.head if hasattr(plane,"audit") else plane.head,"retrieved_count":len(found),"content_count":len(case.content),"calls":{"add":len(case.content),"search":1},"latency_ms":elapsed,"mode":"active" if arm in {"vanilla","static","cmd"} else "shadow_observe_only",**score}; result.append(row)
            # coexisting is a conflict control, not a forced process-fault incident.
            if score["recall"] is False and case.task != "coexisting":
                subtype=ProcessFaultSubtype(score["subtype"]); decision=TriageDecision(IncidentMechanism.PROCESS_FAULT,RepairFamily.PIPELINE_PATCH,"post-retrieval offline MemFail scorer confirmed a retrieval-side miss",False,True,process_fault_subtype=subtype)
                ledger.append(event_id=f"{case.case_id}:{arm}",incident_id=f"incident:{case.case_id}:{arm}",decision=decision,provenance={"case_id":case.case_id,"arm":arm,"scope_root":content_sha256(scope),"memory_root":plane.root(scope),"audit_head":row["audit_head"]},syndrome={"retrieval_side_failure":True,"answerer":"unavailable"},source_manifest_root=manifest_root)
        journal.append(case.position,case.case_id,result)
        checkpoints.commit(RunCheckpoint("memfail-m0-r1",manifest_root,binding["case_root"],case.position,case.position,{}, {a:{"mode":"active" if a!="ghost" else "shadow_observe_only"} for a in arms},{},"0"*64,"0"*64,{},outcome_head=journal.head,outcome_count=len(journal.events)))
    allrows=[r for e in journal.events for r in e["rows"]]; report={}
    for arm in arms:
        ar=[r for r in allrows if r["arm"]==arm]; groups=defaultdict(list)
        for r in ar: groups[(r["family"],r["subtype"],r.get("hop_count"),r.get("condition_met"),r.get("persona_kind"))].append(r)
        report[arm]={"calls":sum(sum(r["calls"].values()) for r in ar),"latency_ms":sum(r["latency_ms"] for r in ar),"incident_count":sum(1 for e in ledger.events if e["provenance"].get("arm")==arm),"strata":[{"family":k[0],"subtype":k[1],"hop_count":k[2],"condition_met":k[3],"persona_kind":k[4],"scorable":sum(x["scorable"] for x in v),"recall_at_k":sum(x["recall"] is True for x in v)/len(v) if v else None,"mrr":sum(x["mrr"] or 0 for x in v)/len(v) if v else None,"unsafe_answer":"unavailable" if k[0]=="persona" else None} for k,v in sorted(groups.items())]}
    manifest={"schema_version":"cmd-memfail-m0-r1-v1",**binding,"manifest_root":manifest_root,"physical_rows":492,"scored_prompts":len(cases),"outcome_root":journal.head,"incident_root":ledger.head_hash,"arms":report,"label_firewall":{"data_plane_forbidden":["ground_truth","correct_choice","is_misleading","family"],"scorer_after_all_io":True},"headline_warning":"This runner is retrieval-side only; smoke runs are not headline results."}
    atomic_json_write(output/"manifest.json",manifest,ensure_ascii=False,allow_nan=False,indent=2,trailing_newline=True); return manifest

def main(argv: list[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--data-root",type=Path,required=True); p.add_argument("--backend",choices=("in-memory","mem0"),default="in-memory"); p.add_argument("--arms",default="vanilla,static,cmd"); p.add_argument("--limit",type=int,default=0,help="Per-family smoke limit; 0 means all rows."); p.add_argument("--run-mode",choices=("fresh","resume"),default="fresh"); p.add_argument("--output",type=Path,required=True); p.add_argument("--top-k",type=int,default=5); p.add_argument("--mem0-config",type=Path); a=p.parse_args(argv)
    run(a.data_root,a.output,backend=a.backend,arms=tuple(x for x in a.arms.split(",") if x),limit=a.limit,resume=a.run_mode=="resume",top_k=a.top_k,mem0_config=json.loads(a.mem0_config.read_text()) if a.mem0_config else None); return 0
if __name__=="__main__": raise SystemExit(main())
