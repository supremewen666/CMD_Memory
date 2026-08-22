"""Scorable, streaming LongMemEval M0/R1 minimum end-to-end runner.

The input side may read only question/session fields. Oracle sessions are
indexed separately and consulted only after retrieval for offline scoring.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from cmd_audit.core.state_codec import atomic_json_write, content_sha256
from experiments.memory_data_plane import AuditedInMemoryDataPlane, Mem0DataPlane, MemoryRecord
from experiments.v4_run_checkpoint import OutcomeJournal, RunCheckpoint, RunCheckpointStore

DATE_RE = re.compile(r"^(\d{4})/(\d{2})/(\d{2}) \([^)]+\) (\d{2}):(\d{2})$")
INPUT_FIELDS = frozenset(
    {"question_id", "question", "haystack_dates", "haystack_session_ids", "haystack_sessions"}
)
SCORER_ONLY_FIELDS = frozenset({"answer", "answer_session_ids"})
VALID_ARMS = ("vanilla", "static", "cmd", "ghost")


def iter_json_array(path: Path, *, chunk_size: int = 4 * 1024 * 1024) -> Iterator[Mapping[str, Any]]:
    """Yield objects from one top-level JSON array without loading it whole."""
    decoder = json.JSONDecoder()
    with Path(path).open(encoding="utf-8") as handle:
        buffer = ""
        position = 0
        started = False
        need_value = True
        eof = False
        while True:
            if position > chunk_size:
                buffer, position = buffer[position:], 0
            if position >= len(buffer) and not eof:
                block = handle.read(chunk_size)
                if block:
                    buffer += block
                else:
                    eof = True
            while position < len(buffer) and buffer[position].isspace():
                position += 1
            if not started:
                if position >= len(buffer):
                    if eof:
                        raise ValueError(f"{path}: empty JSON input")
                    continue
                if buffer[position] != "[":
                    raise ValueError(f"{path}: expected a top-level JSON array")
                position += 1
                started = True
                continue
            while position < len(buffer) and buffer[position].isspace():
                position += 1
            if position < len(buffer) and buffer[position] == "]":
                position += 1
                trailing = buffer[position:] + handle.read()
                if trailing.strip():
                    raise ValueError(f"{path}: trailing content after JSON array")
                return
            if not need_value:
                if position >= len(buffer):
                    if eof:
                        raise ValueError(f"{path}: unterminated JSON array")
                    continue
                if buffer[position] != ",":
                    raise ValueError(f"{path}: expected ',' between array items")
                position += 1
                need_value = True
                continue
            try:
                value, end = decoder.raw_decode(buffer, position)
            except json.JSONDecodeError as error:
                if eof:
                    raise ValueError(f"{path}: invalid JSON array item: {error}") from error
                block = handle.read(chunk_size)
                if block:
                    buffer += block
                    continue
                eof = True
                continue
            if not isinstance(value, Mapping):
                raise ValueError(f"{path}: every array item must be an object")
            yield value
            position = end
            need_value = False


def _canonical_session(session: Any) -> str:
    return json.dumps(
        session, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )


def _date_key(value: str, original_index: int) -> tuple[int, int, int, int, int, int]:
    matched = DATE_RE.fullmatch(value)
    if not matched:
        raise ValueError(f"invalid LongMemEval date: {value!r}")
    return (*map(int, matched.groups()), original_index)


def _ordered_sessions(row: Mapping[str, Any]) -> list[tuple[str, str, Any]]:
    dates = row.get("haystack_dates")
    session_ids = row.get("haystack_session_ids")
    sessions = row.get("haystack_sessions")
    if not isinstance(dates, list) or not isinstance(session_ids, list) or not isinstance(sessions, list):
        raise ValueError("haystack_dates/session_ids/sessions must be lists")
    if not (len(dates) == len(session_ids) == len(sessions)):
        raise ValueError("LongMemEval session arrays must have equal length")
    indexed: list[tuple[tuple[int, int, int, int, int, int], str, str, Any]] = []
    for index, (date, session_id, session) in enumerate(zip(dates, session_ids, sessions, strict=True)):
        if not isinstance(date, str) or not isinstance(session_id, str):
            raise ValueError("LongMemEval dates and session IDs must be strings")
        indexed.append((_date_key(date, index), date, session_id, session))
    indexed.sort(key=lambda item: item[0])
    return [(date, session_id, session) for _, date, session_id, session in indexed]


def _oracle_index(path: Path) -> dict[str, frozenset[str]]:
    result: dict[str, frozenset[str]] = {}
    for row in iter_json_array(path):
        question_id = row.get("question_id")
        sessions = row.get("haystack_sessions")
        if not isinstance(question_id, str) or not isinstance(sessions, list):
            raise ValueError("oracle rows require question_id and haystack_sessions")
        if question_id in result:
            raise ValueError(f"duplicate oracle question_id: {question_id}")
        result[question_id] = frozenset(
            content_sha256(_canonical_session(session), ensure_ascii=False, allow_nan=False)
            for session in sessions
        )
    return result


def _oracle_for_question(path: Path, question_id: str) -> frozenset[str]:
    """Read scorer evidence only after the case's write/search phase."""
    for row in iter_json_array(path):
        if row.get("question_id") == question_id:
            sessions=row.get("haystack_sessions")
            if not isinstance(sessions,list): raise ValueError("oracle row requires haystack_sessions")
            return frozenset(content_sha256(_canonical_session(session),ensure_ascii=False,allow_nan=False) for session in sessions)
    raise ValueError(f"input question_id missing from oracle: {question_id}")


def _safe_instance_name(question_id: str) -> str:
    return hashlib.sha256(question_id.encode("utf-8")).hexdigest()[:20]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _case_stream(data: Path, limit: int) -> list[dict[str, Any]]:
    """Freeze public input and chronology roots before opening the oracle sidecar."""
    cases = []
    for position, row in enumerate(iter_json_array(data), 1):
        question_id, question = row.get("question_id"), row.get("question")
        if not isinstance(question_id, str) or not isinstance(question, str):
            raise ValueError("input rows require string question_id and question")
        ordered = _ordered_sessions(row)
        raw = [(date, session_id, content_sha256(_canonical_session(session), ensure_ascii=False, allow_nan=False))
               for date, session_id, session in zip(row["haystack_dates"], row["haystack_session_ids"], row["haystack_sessions"], strict=True)]
        sorted_rows = [(date, session_id, content_sha256(_canonical_session(session), ensure_ascii=False, allow_nan=False))
                       for date, session_id, session in ordered]
        cases.append({"position": position, "question_id": question_id, "question": question, "ordered": ordered,
                      "raw_order_root": content_sha256(raw, ensure_ascii=False, allow_nan=False),
                      "sorted_order_root": content_sha256(sorted_rows, ensure_ascii=False, allow_nan=False)})
        if len(cases) >= limit:
            break
    if len({item["question_id"] for item in cases}) != len(cases):
        raise ValueError("duplicate input question_id")
    return cases


def _score_records(
    records: Sequence[MemoryRecord], oracle_hashes: frozenset[str], input_hashes: set[str]
) -> tuple[bool, bool | None, int]:
    scorable = bool(oracle_hashes & input_hashes)
    relevant = sum(record.source_hash in oracle_hashes for record in records)
    return scorable, (relevant > 0 if scorable else None), relevant


def run(
    data: Path,
    oracle: Path,
    output: Path,
    backend: str = "in-memory",
    dry_run: bool = False,
    *,
    limit: int = 5,
    resume: bool = False,
    data_sha256: str | None = None,
    oracle_sha256: str | None = None,
    arms: Sequence[str] = VALID_ARMS,
    top_k: int | None = None,
    mem0_config: Mapping[str, object] | None = None,
) -> dict[str, object]:
    if limit < 1 or (top_k is not None and top_k < 1): raise ValueError("limit and top-k must be positive")
    arms = tuple(arms); top_k = top_k or limit
    if not arms or len(set(arms)) != len(arms) or any(arm not in VALID_ARMS for arm in arms):
        raise ValueError("arms must be unique members of vanilla,static,cmd,ghost")
    output = Path(output)
    audits_dir = output / "audits"
    # P3C consumes these frozen deployment-visible retrieval snapshots.  They
    # deliberately contain no oracle / answer fields and are written before
    # the scorer-only sidecar is opened.
    retrieval_dir = output / "retrieval"
    cases = _case_stream(data, limit)
    stream_root = content_sha256([{k:v for k,v in case.items() if k not in {"question", "ordered"}} for case in cases], ensure_ascii=False, allow_nan=False)
    binding = {"data_root": data_sha256 or _file_sha256(data), "oracle_root": oracle_sha256 or _file_sha256(oracle), "case_stream_root": stream_root, "arms": arms, "backend": backend, "top_k": top_k}
    manifest_root = content_sha256(binding, ensure_ascii=False, allow_nan=False)
    if resume:
        checkpoint = RunCheckpointStore(output / "checkpoint").load_latest(manifest_sha256=manifest_root, case_stream_sha256=stream_root)
        start = checkpoint.next_position
    else:
        if output.exists() and any(output.iterdir()): raise ValueError("fresh refuses a non-empty output directory")
        start = 0
    audits_dir.mkdir(parents=True, exist_ok=True)
    retrieval_dir.mkdir(parents=True, exist_ok=True)
    instances_dir = output / "instances"; instances_dir.mkdir(parents=True, exist_ok=True)
    journal, checkpoints = OutcomeJournal(output / "outcomes.jsonl"), RunCheckpointStore(output / "checkpoint")
    for case in cases[start:]:
        position, question_id, question = int(case["position"]), str(case["question_id"]), str(case["question"])
        rows=[]; pending_scores=[]
        for arm in arms:
            scope=f"longmemeval:{arm}:{_safe_instance_name(question_id)}"
            pending=audits_dir/f"{_safe_instance_name(question_id)}.{arm}.jsonl.pending"; final=pending.with_suffix("")
            pending.unlink(missing_ok=True)
            if backend == "in-memory": plane=AuditedInMemoryDataPlane(pending,sync_each_event=False)
            elif backend == "mem0":
                if not mem0_config: raise ValueError("--backend mem0 requires --mem0-config")
                plane=Mem0DataPlane(namespace=scope,user_id=scope,config=mem0_config,audit_path=pending)
            else: raise ValueError("unknown backend")
            input_hashes=set()
            if not dry_run:
                for _,_,session in case["ordered"]: input_hashes.add(plane.add(content=_canonical_session(session),scope=scope).source_hash)
                found=plane.search(query=question,scope=scope,limit=top_k)
            else:
                input_hashes={content_sha256(_canonical_session(session),ensure_ascii=False,allow_nan=False) for _,_,session in case["ordered"]}; found=()
            plane.flush()
            if pending.exists(): os.replace(pending,final)
            arm_dir = retrieval_dir / arm; arm_dir.mkdir(parents=True, exist_ok=True)
            atomic_json_write(
                arm_dir / f"{_safe_instance_name(question_id)}.json",
                {"schema_version": "cmd-longmemeval-retrieval-v1", "question_id": question_id,
                 "arm": arm, "memory_root": plane.root(scope), "scope_root": content_sha256(scope),
                 "top_k": top_k, "records": [{"memory_id": record.memory_id, "content": record.content,
                 "source_hash": record.source_hash} for record in found]},
                ensure_ascii=False, allow_nan=False, indent=2, trailing_newline=True,
            )
            pending_scores.append((arm,scope,plane,found,input_hashes))
        oracle_hashes=_oracle_for_question(oracle,question_id) # scorer-only sidecar after all writes/searches.
        for arm,scope,plane,found,input_hashes in pending_scores:
            scorable,recall,relevant=_score_records(found,oracle_hashes,input_hashes)
            first_rank=next((index for index,record in enumerate(found,1) if record.source_hash in oracle_hashes),None)
            rows.append({"arm":arm,"scope_root":content_sha256(scope),"memory_root":plane.root(scope),"audit_head":plane.audit.head if hasattr(plane,"audit") else plane.head,"session_count":len(case["ordered"]),"m0_retained":len(case["ordered"]),"retrieved_count":len(found),"retrieved_relevant_count":relevant,"scorable":scorable,"recall":recall,"mrr":((1.0/first_rank) if first_rank and scorable else (0.0 if scorable else None)),"calls":{"add":0 if dry_run else len(case["ordered"]),"search":0 if dry_run else 1},"mode":"active" if arm in {"vanilla","static"} else "shadow_observe_only"})
            if arm == "vanilla":
                atomic_json_write(instances_dir / f"{_safe_instance_name(question_id)}.json", {"schema_version":"cmd-longmemeval-instance-v3","question_id":question_id,"session_count":len(case["ordered"]),"m0_retained":len(case["ordered"]),"retrieved_count":len(found),"retrieved_relevant_count":relevant,"scorable":scorable,f"r1_recall_at_{top_k}":recall,"memory_root":plane.root(scope),"raw_order_root":case["raw_order_root"],"sorted_order_root":case["sorted_order_root"]}, ensure_ascii=False,allow_nan=False,indent=2,trailing_newline=True)
        journal.append(position,question_id,rows)
        checkpoints.commit(RunCheckpoint("longmemeval-m0-r1",manifest_root,stream_root,position,position,{}, {a:{"mode":"active" if a in {"vanilla","static"} else "shadow_observe_only"} for a in arms},{},"0"*64,"0"*64,{},outcome_head=journal.head,outcome_count=len(journal.events)))
    all_rows=[row for event in journal.events for row in event["rows"]]
    manifest: dict[str, object] = {
        "schema_version": "cmd-longmemeval-m0-r1-v3", **binding, "manifest_root":manifest_root,"dry_run":dry_run,"question_count":len(journal.events),"outcome_root":journal.head,
        "provenance_warning":"dataset manifest closure is recorded but intentionally non-blocking for P3A", "arms":{},
        "input_field_allowlist": sorted(INPUT_FIELDS),
        "scorer_only_fields": sorted(SCORER_ONLY_FIELDS),
    }
    for arm in arms:
        rows=[r for r in all_rows if r["arm"]==arm]; sc=[r for r in rows if r["scorable"]]
        manifest["arms"][arm]={"mode":rows[0]["mode"] if rows else "unknown","scorable":len(sc),"unscorable":len(rows)-len(sc),"retention":sum(r["m0_retained"] for r in rows)/sum(r["session_count"] for r in rows) if rows else None,f"recall_at_{top_k}":sum(r["recall"] is True for r in sc)/len(sc) if sc else None,"mrr":sum(r["mrr"] for r in sc)/len(sc) if sc else None,"call_count":sum(sum(r["calls"].values()) for r in rows)}
    vanilla=manifest["arms"].get("vanilla", {})
    manifest.update({"retrieval_limit":top_k,"scorable_count":vanilla.get("scorable",0),"unscorable_fraction":(vanilla.get("unscorable",0)/len(journal.events) if journal.events else None),f"r1_recall_at_{top_k}":vanilla.get(f"recall_at_{top_k}"),"instance_artifact_count":len(journal.events)})
    atomic_json_write(output / "manifest.json", manifest, ensure_ascii=False, allow_nan=False, indent=2, trailing_newline=True)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backend", choices=("in-memory", "mem0"), default="in-memory")
    parser.add_argument("--arms", default=",".join(VALID_ARMS))
    parser.add_argument("--top-k", type=int)
    parser.add_argument("--mem0-config", type=Path)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--data-sha256")
    parser.add_argument("--oracle-sha256")
    parser.add_argument("--run-mode", choices=("fresh", "resume"), default="fresh")
    args = parser.parse_args(argv)
    if args.run_mode == "fresh" and args.output.exists() and any(args.output.iterdir()):
        raise ValueError("fresh refuses a non-empty output directory")
    run(
        args.data,
        args.oracle,
        args.output,
        args.backend,
        args.dry_run,
        limit=args.limit,
        resume=args.run_mode == "resume",
        data_sha256=args.data_sha256,
        oracle_sha256=args.oracle_sha256,
        arms=tuple(item for item in args.arms.split(",") if item),
        top_k=args.top_k,
        mem0_config=json.loads(args.mem0_config.read_text(encoding="utf-8")) if args.mem0_config else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
