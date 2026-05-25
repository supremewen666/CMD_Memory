"""Memory-Probe 3x2 grid-comparison baseline (2603.02473).

Aggregate diagnostic that tests write-strategy x retrieval-method
combinations independently of CMD's counterfactual replay attribution.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from cmd_audit.models import MemoryItem, ProbeCase
from cmd_audit.retrieval_baselines import (
    build_tfidf_vectors,
    compute_bm25_scores,
    cosine_similarity,
    tokenize,
)
from cmd_audit.scoring import answer_score as _answer_score, evidence_recall_from_text

WRITE_STRATEGIES = ("fact_extraction", "summarization", "raw_chunks")
RETRIEVAL_METHODS = ("cosine", "bm25")


# ── Data structures ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class MemoryProbeCellResult:
    """Result of a single (write_strategy, retrieval_method) cell."""

    write_strategy: str
    retrieval_method: str
    answer_score: float
    evidence_score: float
    top_item_text: str


@dataclass(frozen=True)
class MemoryProbeCaseResult:
    """All 6 cell results for a single probe case plus the best cell."""

    case_id: str
    cell_results: tuple[MemoryProbeCellResult, ...]
    best_cell: MemoryProbeCellResult


@dataclass(frozen=True)
class MemoryProbeBaselineResult:
    """Aggregate memory-probe results across all probe cases."""

    case_results: tuple[MemoryProbeCaseResult, ...]
    best_cell_accuracy: float
    best_write_strategy: str
    best_retrieval_method: str


# ── Write strategies ─────────────────────────────────────────────────────


def _write_fact_extraction(case: ProbeCase) -> tuple[MemoryItem, ...]:
    """Mem0-style: split raw event text into atomic fact claims."""
    items: list[MemoryItem] = []
    idx = 0
    for event in case.raw_events:
        for sentence in _split_sentences(event.text):
            items.append(
                MemoryItem(
                    memory_id=f"fact_{case.case_id}_{idx}",
                    text=sentence,
                    source_event_ids=(event.event_id,),
                    store="episodic",
                )
            )
            idx += 1
    return tuple(items)


def _write_summarization(case: ProbeCase) -> tuple[MemoryItem, ...]:
    """MemGPT-style: one memory item per raw event, noise-filtered."""
    items: list[MemoryItem] = []
    for event in case.raw_events:
        lines = [
            line.strip() for line in event.text.split("\n") if len(line.split()) >= 3
        ]
        if lines:
            items.append(
                MemoryItem(
                    memory_id=f"summary_{event.event_id}",
                    text=" ".join(lines),
                    source_event_ids=(event.event_id,),
                    store="episodic",
                )
            )
    return tuple(items)


def _write_raw_chunks(case: ProbeCase) -> tuple[MemoryItem, ...]:
    """Raw chunks: one memory item per raw event, text unchanged."""
    return tuple(
        MemoryItem(
            memory_id=f"raw_{event.event_id}",
            text=event.text,
            source_event_ids=(event.event_id,),
            store="episodic",
        )
        for event in case.raw_events
    )


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"[.;\n]+", text)
    return [p.strip() for p in parts if len(p.split()) >= 2]


# ── Retrieval helpers ────────────────────────────────────────────────────


def _retrieve_top1_cosine(
    memory_items: tuple[MemoryItem, ...],
    query: str,
) -> tuple[int, str]:
    """Return (index, text) of top-1 item by TF-IDF cosine similarity."""
    if not memory_items:
        return -1, ""
    items_list = list(memory_items)
    query_vec, doc_vecs = build_tfidf_vectors(items_list, query)
    scores = [cosine_similarity(query_vec, dv) for dv in doc_vecs]
    best_idx = max(range(len(scores)), key=lambda i: scores[i])
    return best_idx, items_list[best_idx].text


def _retrieve_top1_bm25(
    memory_items: tuple[MemoryItem, ...],
    query: str,
) -> tuple[int, str]:
    """Return (index, text) of top-1 item by BM25."""
    if not memory_items:
        return -1, ""
    query_tokens = tokenize(query)
    if not query_tokens:
        return 0, memory_items[0].text
    doc_tokens_list = [tokenize(item.text) for item in memory_items]
    scores = compute_bm25_scores(query_tokens, doc_tokens_list)
    best_idx = max(range(len(scores)), key=lambda i: scores[i])
    return best_idx, memory_items[best_idx].text


# ── Grid runner ──────────────────────────────────────────────────────────


def run_memory_probe_case(case: ProbeCase) -> MemoryProbeCaseResult:
    """Run all 6 (write_strategy x retrieval_method) cells for one case."""
    write_strategies = {
        "fact_extraction": _write_fact_extraction,
        "summarization": _write_summarization,
        "raw_chunks": _write_raw_chunks,
    }
    retrieval_methods = {
        "cosine": _retrieve_top1_cosine,
        "bm25": _retrieve_top1_bm25,
    }

    cell_results: list[MemoryProbeCellResult] = []
    for ws_name, ws_fn in write_strategies.items():
        memory_items = ws_fn(case)
        for rm_name, rm_fn in retrieval_methods.items():
            _idx, top_text = rm_fn(memory_items, case.query)
            ans_score = _answer_score(top_text, case.gold_answer)
            ev_score = evidence_recall_from_text(case.gold_evidence, top_text)
            cell_results.append(
                MemoryProbeCellResult(
                    write_strategy=ws_name,
                    retrieval_method=rm_name,
                    answer_score=ans_score,
                    evidence_score=ev_score,
                    top_item_text=top_text,
                )
            )

    best_cell = max(cell_results, key=lambda c: c.answer_score)
    return MemoryProbeCaseResult(
        case_id=case.case_id,
        cell_results=tuple(cell_results),
        best_cell=best_cell,
    )


# ── Aggregate baseline ───────────────────────────────────────────────────


def run_memory_probe_baselines(
    cases: list[ProbeCase],
) -> MemoryProbeBaselineResult:
    """Run memory-probe 3x2 grid across all cases, compute aggregate best-cell accuracy."""
    case_results = tuple(run_memory_probe_case(case) for case in cases)

    cell_accuracy: dict[tuple[str, str], float] = {}
    for ws in WRITE_STRATEGIES:
        for rm in RETRIEVAL_METHODS:
            total = len(cases)
            correct = sum(
                1
                for cr in case_results
                for cell in cr.cell_results
                if cell.write_strategy == ws
                and cell.retrieval_method == rm
                and cell.answer_score == 1.0
            )
            cell_accuracy[(ws, rm)] = correct / total if total else 0.0

    best_pair = max(cell_accuracy, key=cell_accuracy.get)
    return MemoryProbeBaselineResult(
        case_results=case_results,
        best_cell_accuracy=cell_accuracy[best_pair],
        best_write_strategy=best_pair[0],
        best_retrieval_method=best_pair[1],
    )
