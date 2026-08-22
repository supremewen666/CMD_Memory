"""P4A provider-neutral no-Mem0 retrieval-baseline confirmation.

The retrieval ABI receives only deployment-visible query/content records. Gold
evidence is opened by the dataset-specific scorer *after* a strategy returned
its ranking. ``oracle-ceiling`` is scorer-only: it produces aggregate upper
bound metrics and never materialises prediction context or retrieval records.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

from cmd_audit.core.state_codec import append_jsonl_fsync, atomic_json_write, content_sha256
from experiments.memory_data_plane import MemoryRecord
from experiments.run_longmemeval_m0_r1 import (_canonical_session, _case_stream,
    _file_sha256, _oracle_index, _safe_instance_name, _score_records, iter_json_array)
from experiments.run_memfail_m0_r1 import (_csv_path, _file_hash, _read_rows,
    _score_case, _visible_cases)

TOKEN = __import__("re").compile(r"[\w]+")

def _tokens(text: str) -> tuple[str, ...]: return tuple(TOKEN.findall(text.lower()))

class RetrievalStrategy(Protocol):
    """Small strategy ABI over the P3 data-plane's ``MemoryRecord`` shape."""
    name: str
    def index(self, records: Sequence[MemoryRecord]) -> None: ...
    def search(self, query: str, limit: int) -> tuple[MemoryRecord, ...]: ...
    def index_bytes(self) -> int: ...

class LexicalStrategy:
    name = "lexical"
    def index(self, records: Sequence[MemoryRecord]) -> None: self.records = tuple(records)
    def search(self, query: str, limit: int) -> tuple[MemoryRecord, ...]:
        terms = set(_tokens(query))
        return tuple(sorted(self.records, key=lambda r: (-len(terms & set(_tokens(r.content))), r.memory_id))[:limit])
    def index_bytes(self) -> int: return sum(len(r.content.encode()) for r in self.records)

class BM25Strategy:
    """Stdlib Okapi BM25 (Robertson/Sparck Jones), deterministic ID tie-break."""
    name = "bm25"
    def __init__(self, k1: float = 1.2, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
    def index(self, records: Sequence[MemoryRecord]) -> None:
        self.records = tuple(records); self.tfs = tuple(Counter(_tokens(r.content)) for r in records)
        self.lengths = tuple(sum(tf.values()) for tf in self.tfs); self.avgdl = sum(self.lengths) / len(self.lengths) if self.lengths else 0.0
        self.df = Counter(term for tf in self.tfs for term in tf)
    def _score(self, terms: Sequence[str], i: int) -> float:
        score = 0.0; n = len(self.records); dl = self.lengths[i]
        for term in set(terms):
            tf, df = self.tfs[i][term], self.df[term]
            if not tf: continue
            idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
            score += idf * tf * (self.k1 + 1.0) / (tf + self.k1 * (1.0 - self.b + self.b * dl / self.avgdl))
        return score
    def search(self, query: str, limit: int) -> tuple[MemoryRecord, ...]:
        terms = _tokens(query)  # one query tokenization; document stats are indexed once.
        return tuple(r for _, r in sorted(((self._score(terms, i), r) for i, r in enumerate(self.records)), key=lambda x: (-x[0], x[1].memory_id))[:limit])
    def index_bytes(self) -> int: return sum(len(r.content.encode()) for r in self.records) + sum(len(tf) * 32 for tf in self.tfs)

class MiniLMStrategy:
    name = "minilm"
    model_name = "sentence-transformers/all-MiniLM-L6-v2"
    def __init__(self) -> None:
        if importlib.util.find_spec("sentence_transformers") is None:
            raise RuntimeError("unavailable: sentence_transformers is not installed")
        from sentence_transformers import SentenceTransformer
        try: self.model = SentenceTransformer(self.model_name, local_files_only=True)
        except Exception as exc: raise RuntimeError("unavailable: all-MiniLM-L6-v2 weights are not available locally") from exc
    def index(self, records: Sequence[MemoryRecord]) -> None:
        self.records = tuple(records); self.embeddings = self.model.encode([r.content for r in records], normalize_embeddings=True, show_progress_bar=False)
    def search(self, query: str, limit: int) -> tuple[MemoryRecord, ...]:
        vector = self.model.encode([query], normalize_embeddings=True, show_progress_bar=False)[0]
        scores = [float(vector @ embedding) for embedding in self.embeddings]
        return tuple(r for _, r in sorted(zip(scores, self.records), key=lambda x: (-x[0], x[1].memory_id))[:limit])
    def index_bytes(self) -> int: return int(getattr(self.embeddings, "nbytes", 0))

def _strategy(name: str) -> RetrievalStrategy:
    if name == "lexical": return LexicalStrategy()
    if name == "bm25": return BM25Strategy()
    if name == "minilm": return MiniLMStrategy()
    raise ValueError("oracle-ceiling is scorer-only and has no RetrievalStrategy")

def _records(contents: Sequence[str], scope: str) -> tuple[MemoryRecord, ...]:
    return tuple(MemoryRecord(f"m-{i}-{content_sha256(text)[:12]}", text, content_sha256(text), scope) for i, text in enumerate(contents))

def _aggregate(rows: list[dict[str, Any]], top_k: int) -> dict[str, Any]:
    sc = [r for r in rows if r["scorable"]]
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for r in rows: groups[tuple(r.get(k) for k in ("question_type", "family", "subtype", "hop_count", "condition_met", "persona_kind"))].append(r)
    return {"cases": len(rows), "scorable": len(sc), "unscorable": len(rows)-len(sc), "retention": sum(r["retained"] for r in rows)/sum(r["content_count"] for r in rows) if rows else None,
            f"recall_at_{top_k}": sum(r["recall"] is True for r in sc)/len(sc) if sc else None,
            "mrr": sum(r["mrr"] or 0.0 for r in sc)/len(sc) if sc else None,
            "latency_ms": sum(r["latency_ms"] for r in rows), "calls": sum(r["calls"] for r in rows), "index_bytes": sum(r["index_bytes"] for r in rows),
            "strata": [{"key": list(key), "cases":len(value), "scorable":sum(x["scorable"] for x in value), f"recall_at_{top_k}":sum(x["recall"] is True for x in value if x["scorable"])/sum(x["scorable"] for x in value) if any(x["scorable"] for x in value) else None, "mrr":sum(x["mrr"] or 0.0 for x in value if x["scorable"])/sum(x["scorable"] for x in value) if any(x["scorable"] for x in value) else None} for key,value in sorted(groups.items(), key=str)]}

def _load_rankings(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists(): return {}
    result={}
    for line in path.read_text(encoding="utf-8").splitlines():
        row=json.loads(line)
        if row["case_id"] in result: raise ValueError("duplicate durable ranking")
        result[row["case_id"]]=row
    return result

def _longmemeval(data: Path, oracle: Path, strategy_name: str, limit: int, top_k: int, ranking_path: Path | None = None) -> list[dict[str, Any]]:
    """Retrieve all cases first, then open oracle once for the scorer phase.

    ``ranking_path`` is append-only and gold-free, enabling exactly-once resume
    after a process interruption without changing retrieval order or context.
    """
    out=[]; rankings=_load_rankings(ranking_path) if ranking_path else {}
    cases=_case_stream(data, limit)
    for case in cases:
        prior=rankings.get(str(case["question_id"]))
        if prior is not None:
            out.append(prior); continue
        scope=f"p4a:lme:{_safe_instance_name(case['question_id'])}"; records=_records([_canonical_session(s) for _,_,s in case["ordered"]], scope)
        began=time.perf_counter()
        if strategy_name == "oracle-ceiling": found=(); index_bytes=0
        else:
            strategy=_strategy(strategy_name); strategy.index(records); found=strategy.search(str(case["question"]), top_k); index_bytes=strategy.index_bytes()
        elapsed=(time.perf_counter()-began)*1000
        rank={"case_id":case["question_id"], "found_source_hashes":[r.source_hash for r in found], "input_source_hashes":[r.source_hash for r in records], "retained":len(records),"content_count":len(records),"latency_ms":elapsed,"calls":0,"index_bytes":index_bytes}
        out.append(rank)
        if ranking_path: append_jsonl_fsync(ranking_path, rank, ensure_ascii=False, allow_nan=False)
    # One scorer-only index pass replaces a full oracle scan per case.
    oracle_hashes=_oracle_index(oracle)
    for row in out:
        gold=oracle_hashes[str(row["case_id"])]
        found=tuple(MemoryRecord(f"scorer-{i}", "", source, "scorer") for i,source in enumerate(row["found_source_hashes"]))
        scorable, recall, _ = _score_records(found, gold, set(row["input_source_hashes"]))
        row["scorable"]=scorable
        row["recall"]=True if strategy_name == "oracle-ceiling" and scorable else recall
        row["mrr"]=(1.0 if strategy_name == "oracle-ceiling" and scorable else next((1.0/i for i,r in enumerate(found,1) if r.source_hash in gold), 0.0) if scorable else None)
        row.pop("found_source_hashes", None); row.pop("input_source_hashes", None)
    # question_type is a reporting stratum, so it is opened only after every
    # strategy ranking has been sealed.
    types={str(row.get("question_id")):row.get("question_type") for row in iter_json_array(data)}
    for row in out: row["question_type"] = types.get(str(row["case_id"]))
    return out

def _memfail(root: Path, strategy_name: str, limit: int, top_k: int) -> list[dict[str, Any]]:
    out=[]
    for case in _visible_cases(_read_rows(root), limit):
        records=_records(case.content, f"p4a:memfail:{hashlib.sha256(case.case_id.encode()).hexdigest()[:20]}"); began=time.perf_counter()
        if strategy_name == "oracle-ceiling": found=(); index_bytes=0
        else:
            strategy=_strategy(strategy_name); strategy.index(records); found=strategy.search(case.query, top_k); index_bytes=strategy.index_bytes()
        elapsed=(time.perf_counter()-began)*1000
        score=_score_case(case, found)  # Offline scorer; opens labels only here.
        if strategy_name == "oracle-ceiling": score["recall"] = True if score["scorable"] else None; score["mrr"] = 1.0 if score["scorable"] else None
        out.append({"case_id":case.case_id,"retained":len(records),"content_count":len(records),"latency_ms":elapsed,"calls":0,"index_bytes":index_bytes,**score})
    return out

def run(*, dataset: str, strategy: str, output: Path, limit: int = 0, top_k: int = 5, run_mode: str = "fresh") -> dict[str, Any]:
    if dataset not in {"longmemeval-s","longmemeval-m","memfail"} or strategy not in {"lexical","bm25","minilm","oracle-ceiling"}: raise ValueError("unsupported dataset or strategy")
    if limit < 0 or top_k < 1: raise ValueError("limit must be nonnegative and top-k positive")
    if run_mode == "fresh" and output.exists() and any(output.iterdir()): raise ValueError("fresh refuses non-empty output")
    root=Path(__file__).resolve().parents[2]
    try:
        if dataset.startswith("longmemeval"):
            data=root/"data/external/longmemeval/input"/("longmemeval_s_cleaned.json" if dataset.endswith("s") else "longmemeval_m_cleaned.json")
            count=limit or 10**9
            roots={"data":_file_sha256(data),"oracle":_file_sha256(root/"data/external/longmemeval/oracle/longmemeval_oracle.json")}
            binding={"dataset":dataset,"strategy":strategy,"top_k":top_k,"limit":limit,"input_roots":roots}
            receipt=output/"run_receipt.json"
            if run_mode == "resume":
                if not receipt.exists(): raise ValueError("resume requires root-bound run receipt")
                if json.loads(receipt.read_text()) != binding: raise ValueError("resume manifest mismatch")
            else:
                output.mkdir(parents=True,exist_ok=True); atomic_json_write(receipt,binding,ensure_ascii=False,allow_nan=False,indent=2,trailing_newline=True)
            rows=_longmemeval(data, root/"data/external/longmemeval/oracle/longmemeval_oracle.json", strategy, count, top_k, output/"rankings.jsonl" if strategy != "oracle-ceiling" else None)
        else:
            data=root/"data/external/memfail/datasets"; rows=_memfail(data,strategy,limit,top_k); roots={task:_file_hash(_csv_path(data,task)) for task in _read_rows(data)}
    except RuntimeError as exc:
        if strategy == "minilm" and str(exc).startswith("unavailable:"):
            result={"schema_version":"cmd-p4a-retrieval-baseline-v1","dataset":dataset,"strategy":strategy,"status":"unavailable","reason":str(exc),"command":"python -m experiments.baselines.retrieval_confirmation --dataset %s --strategy minilm --output OUTPUT" % dataset,"network":"prohibited","api":"prohibited","mem0":"prohibited"}; output.mkdir(parents=True,exist_ok=True); atomic_json_write(output/"manifest.json",result,ensure_ascii=False,allow_nan=False,indent=2,trailing_newline=True); return result
        raise
    output.mkdir(parents=True,exist_ok=True)
    offline=strategy == "oracle-ceiling"; summary=_aggregate(rows,top_k)
    manifest={"schema_version":"cmd-p4a-retrieval-baseline-v1","dataset":dataset,"strategy":strategy,"status":"success","top_k":top_k,"limit":limit,"input_roots":roots,"summary":summary,"prediction_context":False if offline else True,"offline_upper_bound":offline,"gold_firewall":{"strategy_receives":"query/content/MemoryRecord only","scorer_after_retrieval":True},"network":"prohibited","api":"prohibited","mem0":"prohibited"}
    if not offline: (output/"rows.jsonl").write_text("".join(json.dumps(r,ensure_ascii=False,sort_keys=True)+"\n" for r in rows),encoding="utf-8")
    atomic_json_write(output/"manifest.json",manifest,ensure_ascii=False,allow_nan=False,indent=2,trailing_newline=True); return manifest

def main(argv: Sequence[str] | None=None) -> int:
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--dataset",choices=("longmemeval-s","longmemeval-m","memfail"),required=True); p.add_argument("--strategy",choices=("lexical","bm25","minilm","oracle-ceiling"),required=True); p.add_argument("--output",type=Path,required=True); p.add_argument("--limit",type=int,default=0); p.add_argument("--top-k",type=int,default=5); p.add_argument("--run-mode",choices=("fresh","resume"),default="fresh"); a=p.parse_args(argv); run(dataset=a.dataset,strategy=a.strategy,output=a.output,limit=a.limit,top_k=a.top_k,run_mode=a.run_mode); return 0
if __name__ == "__main__": raise SystemExit(main())
