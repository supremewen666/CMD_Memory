"""Shared interception logic used by all CMD-Skill Adapters."""

from __future__ import annotations

from cmd_audit.core.models import MemoryItem
from cmd_audit.scoring import evidence_recall_from_text
from .base import ReplayName


def intercept_write_side(
    replay: ReplayName,
    original_items: list[str],
    gold_evidence,
    extracted_memory,
    search_results: tuple[MemoryItem, ...],
) -> list[str]:
    """Route write-side interception for oracle_write/compression/verbatim/injection."""
    if replay == "oracle_write":
        return [
            e.text
            for e in gold_evidence
            if e.source_memory_id is None and e.source_event_id is None
        ]

    if replay == "oracle_compression":
        memory_by_id = {item.memory_id: item for item in extracted_memory}
        recovered = []
        for evidence in gold_evidence:
            if not evidence.source_memory_id:
                continue
            memory = memory_by_id.get(evidence.source_memory_id)
            if memory is None:
                continue
            if evidence_recall_from_text((evidence,), memory.text) < 1.0:
                recovered.append(evidence.text)
        return recovered

    if replay == "verbatim_event_oracle":
        return []

    if replay == "injection_oracle":
        retrieved_ids = {item.memory_id for item in search_results}
        memory_by_id = {item.memory_id: item for item in extracted_memory}
        recovered = []
        for evidence in gold_evidence:
            if evidence.source_memory_id not in retrieved_ids:
                continue
            memory = memory_by_id.get(evidence.source_memory_id or "")
            if memory and evidence_recall_from_text((evidence,), memory.text) >= 1.0:
                recovered.append(memory.text)
        return recovered

    if replay == "safety_off":
        return [e.text for e in gold_evidence]

    return list(original_items)


def intercept_search_side(
    replay: ReplayName,
    original_results: list[MemoryItem],
    gold_evidence,
    extracted_memory,
) -> list[MemoryItem]:
    """Route search-side interception for oracle_retrieval/evidence_given_reasoning."""
    if replay == "oracle_retrieval":
        original_ids = {item.memory_id for item in original_results}
        memory_by_id = {item.memory_id: item for item in extracted_memory}
        recovered: list[MemoryItem] = []
        for evidence in gold_evidence:
            if not evidence.source_memory_id:
                continue
            if evidence.source_memory_id in original_ids:
                continue
            memory = memory_by_id.get(evidence.source_memory_id)
            if memory and evidence_recall_from_text((evidence,), memory.text) >= 1.0:
                recovered.append(memory)
        return recovered

    if replay == "evidence_given_reasoning":
        memory_by_id = {item.memory_id: item for item in extracted_memory}
        augmented = list(original_results)
        for evidence in gold_evidence:
            if not evidence.source_memory_id:
                continue
            memory = memory_by_id.get(evidence.source_memory_id)
            if memory and evidence_recall_from_text((evidence,), memory.text) >= 1.0:
                if memory not in augmented:
                    augmented.append(memory)
        return augmented

    if replay == "oracle_route":
        original_stores = {item.store for item in original_results}
        memory_by_id = {item.memory_id: item for item in extracted_memory}
        routed: list[MemoryItem] = []
        for evidence in gold_evidence:
            if not evidence.source_memory_id:
                continue
            memory = memory_by_id.get(evidence.source_memory_id)
            if memory is None or memory.store in original_stores:
                continue
            if evidence_recall_from_text((evidence,), memory.text) >= 1.0:
                routed.append(memory)
        return routed

    if replay == "oracle_granularity":
        if not any(evidence.granularity_level for evidence in gold_evidence):
            return []
        memory_by_id = {item.memory_id: item for item in extracted_memory}
        recovered: list[MemoryItem] = []
        for evidence in gold_evidence:
            if not evidence.source_memory_id:
                continue
            memory = memory_by_id.get(evidence.source_memory_id)
            if memory and evidence_recall_from_text((evidence,), memory.text) >= 1.0:
                recovered.append(memory)
        return recovered

    if replay == "graph_off":
        all_items = list(original_results) + list(extracted_memory)
        if not any(item.is_graph_expanded for item in all_items):
            return []
        memory_by_id = {item.memory_id: item for item in extracted_memory}
        recovered: list[MemoryItem] = []
        for evidence in gold_evidence:
            if not evidence.source_memory_id:
                continue
            memory = memory_by_id.get(evidence.source_memory_id)
            if memory is None or memory.is_graph_expanded:
                continue
            if evidence_recall_from_text((evidence,), memory.text) >= 1.0:
                recovered.append(memory)
        return recovered

    return list(original_results)
