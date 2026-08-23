"""Gold-firewalled LongMemEval loader for the live observational arena.

The loader scans every visible haystack session with BM25, then exposes only a
frozen top-k recall set and a bounded, larger candidate pool to CMD.  Reference
answers and answer-session ids never participate in retrieval, routing, or
context construction; ``gold_answer`` is carried only for the arena backend's
isolated shadow evaluator.
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import random
from typing import Any, Mapping, Sequence

from cmd_audit.core.models import MemoryItem, RawEvent, RetrievedItem
from cmd_audit.eval.gold_free_observer import ProbeCoordinates
from cmd_audit.hook import post_retrieve_hook
from experiments.arena_runner_common import ArenaCase
from experiments.baselines.retrieval_confirmation import BM25Strategy
from experiments.memory_data_plane import MemoryRecord
from experiments.run_longmemeval_m0_r1 import (
    _ordered_sessions,
    iter_json_array,
)


DEFAULT_RETRIEVAL_TOP_K = 5
DEFAULT_CANDIDATE_POOL_K = 10


def load_longmemeval_arena_cases(
    path: str | Path,
    *,
    seed: int,
    limit: int = 0,
    retrieval_top_k: int = DEFAULT_RETRIEVAL_TOP_K,
    candidate_pool_k: int = DEFAULT_CANDIDATE_POOL_K,
) -> tuple[ArenaCase, ...]:
    """Build live arena cases from all deployment-visible LongMemEval history.

    ``retrieval_top_k`` is the unrepaired BM25 control.  CMD can inspect the
    larger ``candidate_pool_k`` BM25 prefix, but not the remaining corpus and
    never scorer-only answer metadata.  This creates a bounded retrieval-repair
    intervention rather than silently stuffing every session into the prompt.
    """
    if retrieval_top_k < 1:
        raise ValueError("retrieval_top_k must be positive")
    if candidate_pool_k < retrieval_top_k:
        raise ValueError("candidate_pool_k must be >= retrieval_top_k")

    rows = list(iter_json_array(Path(path)))
    random.Random(seed).shuffle(rows)
    selected = rows[: limit or None]
    cases = tuple(
        _build_case(
            row,
            retrieval_top_k=retrieval_top_k,
            candidate_pool_k=candidate_pool_k,
        )
        for row in selected
    )
    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("duplicate LongMemEval question_id")
    return cases


def runtime_projection(case: ArenaCase) -> dict[str, object]:
    """Return the exact answer-free fields that can affect live execution.

    Tests use this projection as a firewall invariant: changing the reference
    answer or answer-session ids must not change any runtime input.
    """
    raw = case.raw
    return {
        "case_id": case.case_id,
        "family_id": case.family_id,
        "base_context": case.base_context,
        "runtime_branch": case.runtime_branch,
        "hook_confidence": case.hook_confidence,
        "query": raw["query"],
        "raw_events": raw["raw_events"],
        "extracted_memory": raw["extracted_memory"],
        "baseline_outputs": raw["baseline_outputs"],
    }


def _build_case(
    row: Mapping[str, Any],
    *,
    retrieval_top_k: int,
    candidate_pool_k: int,
) -> ArenaCase:
    question_id = _required_text(row, "question_id")
    query = _required_text(row, "question")
    gold_answer = _reference_text(row.get("answer"))
    question_type = str(row.get("question_type") or "unknown")

    ordered = _ordered_sessions(row)
    all_items = tuple(
        _session_item(index, date, session_id, session)
        for index, (date, session_id, session) in enumerate(ordered)
    )
    records = tuple(
        MemoryRecord(
            memory_id=item.memory_id,
            content=item.text,
            source_hash="runtime-only",
            scope=f"longmemeval:{question_id}",
        )
        for item in all_items
    )
    retriever = BM25Strategy()
    retriever.index(records)
    ranked = retriever.search(query, min(candidate_pool_k, len(records)))
    by_id = {item.memory_id: item for item in all_items}
    candidate_items = tuple(by_id[record.memory_id] for record in ranked)
    recall_set = candidate_items[: min(retrieval_top_k, len(candidate_items))]
    retrieved_ids = tuple(item.memory_id for item in recall_set)
    injected = _format_retrieved_context(recall_set)
    hook = post_retrieve_hook(
        query,
        tuple(
            RetrievedItem(memory_id=item.memory_id, text=item.text)
            for item in recall_set
        ),
    )
    raw_events = tuple(
        RawEvent(event_id=f"event:{item.memory_id}", text=item.text)
        for item in candidate_items
    )
    raw = {
        "case_id": question_id,
        "query": query,
        "raw_events": [asdict(item) for item in raw_events],
        "extracted_memory": [asdict(item) for item in candidate_items],
        "baseline_outputs": [
            {
                "baseline_name": "bm25",
                "answer": "",
                "retrieved_memory_ids": list(retrieved_ids),
                "answer_score": 0.0,
                "evidence_score": 0.0,
                "injected_context": injected,
            }
        ],
        # Scorer-only: VLLMDualScoreArenaBackend._shadow_score is the sole
        # runtime-backend reader.  The runtime projection deliberately omits it.
        "gold_answer": gold_answer,
        "question_type": question_type,
        "retrieval_protocol": {
            "strategy": "bm25",
            "history_sessions_scanned": len(all_items),
            "retrieval_top_k": retrieval_top_k,
            "candidate_pool_k": candidate_pool_k,
            "answer_session_ids_used": False,
        },
    }
    return ArenaCase(
        arena_id="longmemeval",
        case_id=question_id,
        family_id=f"longmemeval:{question_type}",
        failure_type="unlabeled_observational",
        base_context=(
            f"Query: {query}\n\n"
            f"BM25 Retrieved Memory (top {len(recall_set)}):\n{injected}"
        ),
        coordinates=ProbeCoordinates(question_type=question_type),
        subset=question_type,
        raw=raw,
        runtime_branch=hook.branch,
        hook_confidence=hook.confidence,
    )


def _session_item(
    index: int,
    date: str,
    session_id: str,
    session: object,
) -> MemoryItem:
    text = _render_session(date, session_id, session)
    memory_id = f"session:{index:04d}:{session_id}"
    return MemoryItem(
        memory_id=memory_id,
        text=text,
        source_event_ids=(f"event:{memory_id}",),
        store="episodic",
        passed_safety_filter=False,
    )


def _render_session(date: str, session_id: str, session: object) -> str:
    lines = [f"DATE: {date}", f"SESSION_ID: {session_id}"]
    if not isinstance(session, Sequence) or isinstance(session, (str, bytes)):
        raise ValueError("LongMemEval session must be a message list")
    for message in session:
        if not isinstance(message, Mapping):
            raise ValueError("LongMemEval session messages must be objects")
        role = str(message.get("role") or "unknown").upper()
        content = str(message.get("content") or "")
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _format_retrieved_context(items: Sequence[MemoryItem]) -> str:
    return "\n\n".join(
        f"[{rank}] {item.text}"
        for rank, item in enumerate(items, start=1)
    )


def _required_text(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"LongMemEval row requires non-empty string {key}")
    return value


def _reference_text(value: object) -> str:
    """Normalize the dataset's documented string-or-integer reference ABI."""
    if isinstance(value, str) and value:
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    raise ValueError("LongMemEval answer must be a non-empty string or integer")
