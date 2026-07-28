"""Action-specific durable state used by store-level repair experiments.

Unlike read-time counterfactual actions, this module persists a repair rule and
materializes the repaired retrieval/pipeline state on later queries.  Rules are
scoped by recall-content fingerprint rather than query wording, so paraphrased
queries can reuse them while unrelated families remain sentinel-testable.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace

from ..core.models import MemoryItem, ProbeCase
from ..counterfactual.actions import PipelineAction
from ..counterfactual.operators import OperatorSpec
from .failure_memory import _memory_fingerprint


@dataclass(frozen=True)
class DurableRepairRule:
    fingerprint: str
    operator: OperatorSpec
    source_family: str

    @property
    def content_hash(self) -> str:
        return self.operator.content_hash()


@dataclass(frozen=True)
class DurableStoreSnapshot:
    rules: tuple[DurableRepairRule, ...]


@dataclass(frozen=True)
class MaterializedRepair:
    items: tuple[MemoryItem, ...]
    context: str
    applied_rule_hashes: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.applied_rule_hashes)


class DurableRepairStore:
    """In-memory durable rule store with real snapshot/restore semantics."""

    def __init__(self, *, similarity_threshold: float = 0.8) -> None:
        if not 0.0 <= similarity_threshold <= 1.0:
            raise ValueError("similarity_threshold must be in [0, 1]")
        self.similarity_threshold = similarity_threshold
        self._rules: list[DurableRepairRule] = []

    def snapshot(self) -> DurableStoreSnapshot:
        return DurableStoreSnapshot(tuple(copy.deepcopy(self._rules)))

    def restore(self, snapshot: DurableStoreSnapshot) -> None:
        self._rules = list(copy.deepcopy(snapshot.rules))

    def matches_snapshot(self, snapshot: DurableStoreSnapshot) -> bool:
        return self.fingerprint() == _rules_fingerprint(snapshot.rules)

    def fingerprint(self) -> tuple[tuple[str, str, str], ...]:
        return _rules_fingerprint(tuple(self._rules))

    def __len__(self) -> int:
        return len(self._rules)

    def write_back(
        self,
        *,
        fingerprint: str,
        operator: OperatorSpec,
        source_family: str,
    ) -> bool:
        rule = DurableRepairRule(
            fingerprint=fingerprint,
            operator=operator,
            source_family=source_family,
        )
        key = (fingerprint, rule.content_hash)
        if any((item.fingerprint, item.content_hash) == key for item in self._rules):
            return False
        self._rules.append(rule)
        return True

    def materialize(
        self,
        case: ProbeCase,
        recall: tuple[MemoryItem, ...],
        *,
        base_context: str,
    ) -> MaterializedRepair:
        fingerprint = _memory_fingerprint(tuple(item.text for item in recall))
        matching = tuple(
            rule
            for rule in self._rules
            if _fingerprint_similarity(fingerprint, rule.fingerprint)
            >= self.similarity_threshold
        )
        if not matching:
            return MaterializedRepair(recall, base_context, ())

        items = recall
        renderer_repaired = False
        applied: list[str] = []
        for rule in matching:
            for step in rule.operator.steps:
                items, changed = _materialize_action(
                    case,
                    items,
                    step.generation_point,
                    step.action,
                    rule.operator.item_signal_hints_dict(),
                )
                renderer_repaired = renderer_repaired or changed
            if renderer_repaired:
                applied.append(rule.content_hash)

        context = (
            _render_repaired_context(case.query, items)
            if renderer_repaired
            else base_context
        )
        return MaterializedRepair(items, context, tuple(applied))


def _materialize_action(
    case: ProbeCase,
    items: tuple[MemoryItem, ...],
    generation_point: int,
    action: PipelineAction,
    item_signal_hints: dict[str, float],
) -> tuple[tuple[MemoryItem, ...], bool]:
    if action == PipelineAction.RETRIEVAL_ERROR:
        existing = {item.memory_id for item in items}
        candidates = _hop_local_candidates(case.extracted_memory, generation_point)
        missed = tuple(item for item in candidates if item.memory_id not in existing)
        return items + missed, bool(missed)

    if action == PipelineAction.INJECTION_ERROR:
        ordered = _order_by_signal(items, item_signal_hints)
        return ordered, bool(ordered)

    if action == PipelineAction.GRANULARITY_ERROR:
        event_text = {event.event_id: event.text for event in case.raw_events}
        expanded: list[MemoryItem] = []
        changed = False
        for item in items:
            if len(item.source_event_ids) <= 1:
                expanded.append(item)
                continue
            replacements = [
                MemoryItem(
                    memory_id=f"{item.memory_id}__{event_id}",
                    text=event_text[event_id],
                    source_event_ids=(event_id,),
                    store=item.store,
                    is_graph_expanded=item.is_graph_expanded,
                    passed_safety_filter=False,
                    provenance=item.provenance,
                )
                for event_id in item.source_event_ids
                if event_id in event_text
            ]
            if replacements:
                expanded.extend(replacements)
                changed = True
            else:
                expanded.append(item)
        return tuple(expanded), changed

    if action == PipelineAction.SAFETY_ERROR:
        repaired = tuple(
            replace(item, passed_safety_filter=False)
            if item.passed_safety_filter
            else item
            for item in items
        )
        return repaired, repaired != items

    if action == PipelineAction.ITEM_STALE:
        timestamped = [
            (item.store, item)
            for item in items
            if isinstance(item.store, str) and item.store.endswith("Z")
        ]
        if len(timestamped) < 2:
            return items, False
        newest = max(timestamped, key=lambda pair: pair[0])[1]
        repaired = tuple(
            item
            for item in items
            if item is newest
            or not (isinstance(item.store, str) and item.store.endswith("Z"))
        )
        return repaired, repaired != items

    if action == PipelineAction.ITEM_POISONED:
        repaired = tuple(
            item
            for item in items
            if item_signal_hints.get(item.memory_id, 0.0) >= 0.0
        )
        return repaired, repaired != items

    if action in {
        PipelineAction.ITEM_CONFLICT,
        PipelineAction.ITEM_WRONG,
        PipelineAction.ITEM_COMPRESSION_DISTORTED,
    }:
        ordered = _order_by_signal(items, item_signal_hints)
        return ordered, bool(ordered)

    return items, False


def _hop_local_candidates(
    candidates: tuple[MemoryItem, ...],
    generation_point: int,
) -> tuple[MemoryItem, ...]:
    prefix = f"m_hop{generation_point + 1}_"
    if not any(item.memory_id.startswith("m_hop") for item in candidates):
        return candidates
    return tuple(
        item
        for item in candidates
        if item.memory_id.startswith(prefix)
        or not item.memory_id.startswith("m_hop")
    )


def _order_by_signal(
    items: tuple[MemoryItem, ...],
    hints: dict[str, float],
) -> tuple[MemoryItem, ...]:
    indexed = list(enumerate(items))
    indexed.sort(
        key=lambda pair: (
            -float(hints.get(pair[1].memory_id, 0.0)),
            pair[0],
        )
    )
    return tuple(item for _, item in indexed)


def _render_repaired_context(query: str, items: tuple[MemoryItem, ...]) -> str:
    rendered = "\n".join(f"- [{item.memory_id}] {item.text}" for item in items)
    return f"Query: {query}\n\nRetrieved Memory:\n{rendered}"


def _fingerprint_similarity(left: str, right: str) -> float:
    left_terms = set(left.split())
    right_terms = set(right.split())
    if not left_terms or not right_terms:
        return 0.0
    return len(left_terms & right_terms) / len(left_terms | right_terms)


def _rules_fingerprint(
    rules: tuple[DurableRepairRule, ...],
) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (rule.fingerprint, rule.content_hash, rule.source_family)
        for rule in rules
    )
