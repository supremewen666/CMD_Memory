"""Execution Lineage DAG and trace-mem Citation for CMD-Audit V1."""

from __future__ import annotations

import hashlib
import hmac
import time

from .models import Citation, MemoryItem, ProbeCase, ProvenanceEdge
from .replays import ReplayResult


def _compute_hmac(content: str, session_key: str) -> str:
    return hmac.new(
        session_key.encode(), content.encode(), hashlib.sha256
    ).hexdigest()


class ProvenanceTracker:
    """Mutable collector for provenance edges recorded during counterfactual replay.

    Tracks edges independently from ``MemoryItem`` (which is frozen). When
    provenance needs to be persisted on items, call ``annotate_item()`` to
    produce a new ``MemoryItem`` copy with the accumulated edges baked in.
    """

    def __init__(self, case_id: str):
        self.case_id = case_id
        self._session_key = hashlib.sha256(case_id.encode()).hexdigest()
        self._edges: list[ProvenanceEdge] = []
        self._item_provenance: dict[str, list[ProvenanceEdge]] = {}

    @property
    def session_key(self) -> str:
        return self._session_key

    def record_edge(
        self,
        source_id: str,
        target_id: str,
        operation: str,
        source_text: str,
        char_span: tuple[int, int] = (0, 0),
        trajectory_turn: int = 0,
    ) -> ProvenanceEdge:
        content_hash = _compute_hmac(source_text, self._session_key)
        citation = Citation(
            trajectory_turn=trajectory_turn,
            char_span=char_span,
            content_hash=content_hash,
        )
        edge = ProvenanceEdge(
            source_id=source_id,
            target_id=target_id,
            operation=operation,
            citation=citation,
            timestamp=time.time(),
            source_text=source_text,
        )
        self._edges.append(edge)
        self._item_provenance.setdefault(target_id, []).append(edge)
        return edge

    def get_edges(self) -> tuple[ProvenanceEdge, ...]:
        return tuple(self._edges)

    def annotate_item(
        self, item: MemoryItem, target_id: str | None = None
    ) -> MemoryItem:
        """Return a new MemoryItem with recorded provenance edges attached."""
        tid = target_id or item.memory_id
        edges = tuple(self._item_provenance.get(tid, ()))
        if not edges:
            return item
        return MemoryItem(
            memory_id=item.memory_id,
            text=item.text,
            source_event_ids=item.source_event_ids,
            store=item.store,
            is_graph_expanded=item.is_graph_expanded,
            provenance=edges,
        )


def record_provenance_edge(
    tracker: ProvenanceTracker,
    source_id: str,
    target_id: str,
    operation: str,
    source_text: str,
    char_span: tuple[int, int] = (0, 0),
    trajectory_turn: int = 0,
) -> ProvenanceEdge:
    """Convenience wrapper around ``ProvenanceTracker.record_edge``."""
    return tracker.record_edge(
        source_id=source_id,
        target_id=target_id,
        operation=operation,
        source_text=source_text,
        char_span=char_span,
        trajectory_turn=trajectory_turn,
    )


def detect_tamper(
    edge: ProvenanceEdge, source_text: str, session_key: str
) -> bool:
    """Return True when the recomputed HMAC differs from the stored citation hash."""
    recomputed = _compute_hmac(source_text, session_key)
    return recomputed != edge.citation.content_hash


def compute_provenance_completeness(
    memory_items: tuple[MemoryItem, ...],
) -> float:
    """Fraction of MemoryItems with non-empty provenance edges."""
    if not memory_items:
        return 0.0
    num_with_prov = sum(1 for item in memory_items if item.provenance)
    return num_with_prov / len(memory_items)


def get_graph_distractor_edges(
    case: ProbeCase, graph_off_result: ReplayResult
) -> tuple[ProvenanceEdge, ...]:
    """Identify graph-expanded items in baseline retrieval that acted as distractors.

    Compares baseline ``retrieved_memory_ids`` against items marked
    ``is_graph_expanded``. Items present only through graph expansion are
    recorded as distractor provenance edges when the graph_off replay
    produced positive recovery.
    """
    if graph_off_result.recovery_gain <= 0:
        return ()

    baseline = case.primary_baseline
    baseline_ids = set(baseline.retrieved_memory_ids)
    memory_by_id = {item.memory_id: item for item in case.extracted_memory}

    session_key = hashlib.sha256(case.case_id.encode()).hexdigest()
    now = time.time()
    edges: list[ProvenanceEdge] = []

    for mid in baseline_ids:
        item = memory_by_id.get(mid)
        if item is None:
            continue
        if not item.is_graph_expanded:
            continue
        citation = Citation(
            trajectory_turn=0,
            char_span=(0, len(item.text)),
            content_hash=_compute_hmac(item.text, session_key),
        )
        edge = ProvenanceEdge(
            source_id=mid,
            target_id=f"{case.case_id}__answer",
            operation="retrieve",
            citation=citation,
            timestamp=now,
            source_text=item.text,
        )
        edges.append(edge)

    return tuple(edges)
