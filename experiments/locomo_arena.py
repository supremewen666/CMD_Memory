"""Gold-firewalled LoCoMo loader for live CMD and sealed prediction runs."""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import random
import re
from typing import Any, Mapping, Sequence

from cmd_audit.core.models import MemoryItem, RawEvent, RetrievedItem
from cmd_audit.eval.gold_free_observer import ProbeCoordinates
from cmd_audit.hook import post_retrieve_hook
from experiments.arena_runner_common import ArenaCase
from experiments.baselines.retrieval_confirmation import BM25Strategy
from experiments.memory_data_plane import MemoryRecord


DEFAULT_RETRIEVAL_TOP_K = 5
DEFAULT_CANDIDATE_POOL_K = 10
CATEGORY_NAMES = {
    1: "multi_hop",
    2: "temporal",
    3: "open_domain",
    4: "single_hop",
    5: "adversarial",
}
_SESSION_RE = re.compile(r"^session_(\d+)$")


def load_locomo_arena_cases(
    path: str | Path,
    *,
    seed: int,
    limit: int = 0,
    retrieval_top_k: int = DEFAULT_RETRIEVAL_TOP_K,
    candidate_pool_k: int = DEFAULT_CANDIDATE_POOL_K,
    include_adversarial: bool = True,
) -> tuple[ArenaCase, ...]:
    """Project the official ten-conversation LoCoMo release into arena cases.

    Answers, evidence dialog IDs, observations and summaries are not copied to
    the runtime view.  BM25 sees only timestamped raw dialogue sessions.
    """
    if retrieval_top_k < 1:
        raise ValueError("retrieval_top_k must be positive")
    if candidate_pool_k < retrieval_top_k:
        raise ValueError("candidate_pool_k must be >= retrieval_top_k")
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("LoCoMo input must be a JSON array")
    rows: list[tuple[Mapping[str, Any], int, Mapping[str, Any]]] = []
    for sample in raw:
        if not isinstance(sample, Mapping) or not isinstance(sample.get("qa"), list):
            raise ValueError("LoCoMo samples require a qa list")
        for qa_index, qa in enumerate(sample["qa"]):
            if not isinstance(qa, Mapping):
                raise ValueError("LoCoMo QA rows must be objects")
            category = qa.get("category")
            if include_adversarial or category != 5:
                rows.append((sample, qa_index, qa))
    random.Random(seed).shuffle(rows)
    cases = tuple(
        _build_case(
            sample,
            qa_index,
            qa,
            retrieval_top_k=retrieval_top_k,
            candidate_pool_k=candidate_pool_k,
        )
        for sample, qa_index, qa in rows[: limit or None]
    )
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("duplicate LoCoMo case id")
    return cases


def runtime_projection(case: ArenaCase) -> dict[str, object]:
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
        "full_context": raw["full_context"],
    }


def _build_case(
    sample: Mapping[str, Any],
    qa_index: int,
    qa: Mapping[str, Any],
    *,
    retrieval_top_k: int,
    candidate_pool_k: int,
) -> ArenaCase:
    sample_id = _text(sample, "sample_id")
    query = _text(qa, "question")
    category = qa.get("category")
    if category not in CATEGORY_NAMES:
        raise ValueError("LoCoMo category must be one of 1..5")
    answer = qa.get("answer")
    # The official release leaves most adversarial references null and scores
    # them by whether the model says that the information is unavailable.
    if category == 5 and answer is None:
        answer = "No information available"
    if not isinstance(answer, (str, int)) or isinstance(answer, bool):
        raise ValueError("LoCoMo answer must be a string or integer")
    conversation = sample.get("conversation")
    if not isinstance(conversation, Mapping):
        raise ValueError("LoCoMo sample requires conversation")
    all_items = tuple(_session_items(conversation))
    records = tuple(
        MemoryRecord(
            memory_id=item.memory_id,
            content=item.text,
            source_hash="runtime-only",
            scope=f"locomo:{sample_id}",
        )
        for item in all_items
    )
    retriever = BM25Strategy()
    retriever.index(records)
    ranked = retriever.search(query, min(candidate_pool_k, len(records)))
    by_id = {item.memory_id: item for item in all_items}
    candidate_items = tuple(by_id[row.memory_id] for row in ranked)
    recall_set = candidate_items[: min(retrieval_top_k, len(candidate_items))]
    injected = _format_context(recall_set)
    hook = post_retrieve_hook(
        query,
        tuple(RetrievedItem(memory_id=item.memory_id, text=item.text) for item in recall_set),
    )
    case_id = f"{sample_id}:q{qa_index:04d}"
    raw_events = tuple(
        RawEvent(event_id=f"event:{item.memory_id}", text=item.text)
        for item in candidate_items
    )
    raw_case = {
        "case_id": case_id,
        "query": query,
        "raw_events": [asdict(item) for item in raw_events],
        "extracted_memory": [asdict(item) for item in candidate_items],
        "baseline_outputs": [{
            "baseline_name": "bm25",
            "answer": "",
            "retrieved_memory_ids": [item.memory_id for item in recall_set],
            "answer_score": 0.0,
            "evidence_score": 0.0,
            "injected_context": injected,
        }],
        "full_context": _format_context(all_items),
        # Scorer-only fields.  ``runtime_projection`` intentionally omits all.
        "gold_answer": str(answer),
        "category": int(category),
        "retrieval_protocol": {
            "strategy": "bm25",
            "history_sessions_scanned": len(all_items),
            "retrieval_top_k": retrieval_top_k,
            "candidate_pool_k": candidate_pool_k,
            "evidence_ids_used": False,
            "generated_observations_used": False,
            "generated_summaries_used": False,
        },
    }
    category_name = CATEGORY_NAMES[int(category)]
    return ArenaCase(
        arena_id="locomo",
        case_id=case_id,
        family_id=f"locomo:{category_name}",
        failure_type="unlabeled_observational",
        base_context=f"Query: {query}\n\nBM25 Retrieved Memory:\n{injected}",
        coordinates=ProbeCoordinates(question_type=category_name),
        subset=category_name,
        raw=raw_case,
        runtime_branch=hook.branch,
        hook_confidence=hook.confidence,
    )


def _session_items(conversation: Mapping[str, Any]) -> Sequence[MemoryItem]:
    sessions: list[tuple[int, object]] = []
    for key, value in conversation.items():
        match = _SESSION_RE.fullmatch(str(key))
        if match and isinstance(value, list):
            sessions.append((int(match.group(1)), value))
    items: list[MemoryItem] = []
    for number, session in sorted(sessions):
        lines = [f"DATE: {conversation.get(f'session_{number}_date_time', '')}"]
        for turn in session:
            if not isinstance(turn, Mapping):
                raise ValueError("LoCoMo turns must be objects")
            speaker = str(turn.get("speaker") or "unknown")
            dialog_id = str(turn.get("dia_id") or "")
            text = str(turn.get("text") or "")
            caption = str(turn.get("blip_caption") or "")
            lines.append(f"[{dialog_id}] {speaker}: {text}" + (f" [IMAGE: {caption}]" if caption else ""))
        memory_id = f"session:{number:03d}"
        items.append(MemoryItem(
            memory_id=memory_id,
            text="\n".join(lines),
            source_event_ids=(f"event:{memory_id}",),
            store="episodic",
            passed_safety_filter=False,
        ))
    if not items:
        raise ValueError("LoCoMo conversation has no materialized sessions")
    return items


def _format_context(items: Sequence[MemoryItem]) -> str:
    return "\n\n".join(f"[{rank}] {item.text}" for rank, item in enumerate(items, 1))


def _text(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"LoCoMo row requires non-empty string {key}")
    return value
