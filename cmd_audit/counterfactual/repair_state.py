"""Route A E-1: structured repair state (BUILD SPEC §3.2).

The legacy repair surface is a rendered context string, so a repair's effect
can only be observed by reading prose back out. Route A needs fitness measured
on state, which requires dispositions to be structured fields, transitions to
be append-only trace events, and rendering to be a pure downstream projection
that cannot itself change fitness.
"""

import hashlib
import json
from dataclasses import dataclass, replace

from cmd_audit.eval.state_intent import RuntimeRepairCase

__all__ = [
    "DISPOSITIONS",
    "VISIBLE_DISPOSITIONS",
    "RepairStateError",
    "RepairStateItem",
    "RepairTraceEvent",
    "RepairState",
    "initial_state_from_runtime_case",
    "apply_disposition",
    "replace_item_text",
    "add_item",
    "render_state",
    "count_tokens",
]

DISPOSITIONS = ("active", "demoted", "suppressed", "historical", "conflict")

# Dispositions whose text still reaches the model. "demoted" and "conflict"
# remain visible but are rendered in a lower section; "suppressed" and
# "historical" are withheld from the generation context.
VISIBLE_DISPOSITIONS = ("active", "demoted", "conflict")


class RepairStateError(ValueError):
    """Raised on an illegal state transition or malformed state."""


def _sha256(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _provenance_hash(item_id: str, text: str, source_event_ids: tuple[str, ...]) -> str:
    """Content-bound ancestry hash.

    Binding text into the hash is what makes a silent rewrite detectable: an
    operator that edits an item's content cannot carry the original item's
    provenance forward (§3.4 ``provenance_valid``).
    """
    return _sha256(
        json.dumps(
            {"item_id": item_id, "text": text, "events": list(source_event_ids)},
            sort_keys=True,
        )
    )


@dataclass(frozen=True)
class RepairStateItem:
    item_id: str
    text: str
    source_event_ids: tuple[str, ...]
    store: str
    provenance_hash: str
    rank: int
    disposition: str

    def as_mapping(self) -> dict[str, object]:
        return {
            "item_id": self.item_id,
            "text": self.text,
            "source_event_ids": list(self.source_event_ids),
            "store": self.store,
            "provenance_hash": self.provenance_hash,
            "rank": self.rank,
            "disposition": self.disposition,
        }


@dataclass(frozen=True)
class RepairTraceEvent:
    operator_node_id: str
    predicate_id: str
    matched_item_ids: tuple[str, ...]
    action: str
    before_hash: str
    after_hash: str
    logical_cost: int

    def as_mapping(self) -> dict[str, object]:
        return {
            "operator_node_id": self.operator_node_id,
            "predicate_id": self.predicate_id,
            "matched_item_ids": list(self.matched_item_ids),
            "action": self.action,
            "before_hash": self.before_hash,
            "after_hash": self.after_hash,
            "logical_cost": self.logical_cost,
        }


@dataclass(frozen=True)
class RepairState:
    case_id: str
    items: tuple[RepairStateItem, ...]
    trace: tuple[RepairTraceEvent, ...]
    rendered_context: str
    token_count: int
    state_hash: str


def count_tokens(text: str) -> int:
    """Deterministic whitespace token count.

    Fitness must be reproducible with zero LLM calls, so the budget gate uses a
    fixed tokenizer-free count rather than a model tokenizer.
    """
    return len(text.split())


def render_state(state: RepairState) -> str:
    """Project state onto a context string. Pure; never mutates fitness."""
    return _render_items(state.items)


def _render_items(items: tuple[RepairStateItem, ...]) -> str:
    lines = [
        item.text
        for item in sorted(items, key=lambda i: (i.rank, i.item_id))
        if item.disposition in VISIBLE_DISPOSITIONS
    ]
    return "\n".join(lines)


def _finalize(
    case_id: str,
    items: tuple[RepairStateItem, ...],
    trace: tuple[RepairTraceEvent, ...],
) -> RepairState:
    ordered = tuple(sorted(items, key=lambda i: (i.rank, i.item_id)))
    rendered = _render_items(ordered)
    token_count = count_tokens(rendered)
    state_hash = _sha256(
        json.dumps(
            {
                "case_id": case_id,
                "items": [item.as_mapping() for item in ordered],
                "token_count": token_count,
            },
            sort_keys=True,
        )
    )
    return RepairState(
        case_id=case_id,
        items=ordered,
        trace=trace,
        rendered_context=rendered,
        token_count=token_count,
        state_hash=state_hash,
    )


def initial_state_from_runtime_case(case: RuntimeRepairCase) -> RepairState:
    """Seed state from what the recorded run actually recalled.

    Items with ``retrieved=False`` are the candidate pool. They are not state:
    the generator never saw them, so a repair that needs one must add it (see
    ``add_item``). Seeding them as active would make a retrieval miss
    inexpressible and would hand an untouched state a gold item for free.
    """
    items = tuple(
        RepairStateItem(
            item_id=item.item_id,
            text=item.text,
            source_event_ids=tuple(item.source_event_ids),
            store=item.store,
            provenance_hash=_provenance_hash(
                item.item_id, item.text, tuple(item.source_event_ids)
            ),
            rank=item.rank,
            disposition="active",
        )
        for item in case.items
        if item.retrieved
    )
    return _finalize(case.case_id, items, ())


def _transition(
    state: RepairState,
    *,
    items: tuple[RepairStateItem, ...],
    matched_item_ids: tuple[str, ...],
    action: str,
    operator_node_id: str,
    predicate_id: str,
    logical_cost: int,
) -> RepairState:
    candidate = _finalize(state.case_id, items, state.trace)
    event = RepairTraceEvent(
        operator_node_id=operator_node_id,
        predicate_id=predicate_id,
        matched_item_ids=matched_item_ids,
        action=action,
        before_hash=state.state_hash,
        after_hash=candidate.state_hash,
        logical_cost=logical_cost,
    )
    return _finalize(state.case_id, items, state.trace + (event,))


def apply_disposition(
    state: RepairState,
    *,
    item_ids: tuple[str, ...],
    disposition: str,
    operator_node_id: str,
    predicate_id: str,
) -> RepairState:
    if disposition not in DISPOSITIONS:
        raise RepairStateError(f"unknown disposition: {disposition!r}")
    known = {item.item_id for item in state.items}
    missing = tuple(item_id for item_id in item_ids if item_id not in known)
    if missing:
        raise RepairStateError(f"unknown item ids: {missing}")
    items = tuple(
        replace(item, disposition=disposition) if item.item_id in item_ids else item
        for item in state.items
    )
    return _transition(
        state,
        items=items,
        matched_item_ids=tuple(item_ids),
        action=f"disposition:{disposition}",
        operator_node_id=operator_node_id,
        predicate_id=predicate_id,
        logical_cost=len(item_ids),
    )


def replace_item_text(
    state: RepairState,
    *,
    item_id: str,
    text: str,
    operator_node_id: str,
    predicate_id: str,
) -> RepairState:
    """Rewrite an item's content. Recomputes its provenance hash."""
    if not any(item.item_id == item_id for item in state.items):
        raise RepairStateError(f"unknown item id: {item_id!r}")
    items = tuple(
        replace(
            item,
            text=text,
            provenance_hash=_provenance_hash(item.item_id, text, item.source_event_ids),
        )
        if item.item_id == item_id
        else item
        for item in state.items
    )
    return _transition(
        state,
        items=items,
        matched_item_ids=(item_id,),
        action="rewrite",
        operator_node_id=operator_node_id,
        predicate_id=predicate_id,
        logical_cost=1,
    )


def add_item(
    state: RepairState,
    *,
    item_id: str,
    text: str,
    source_event_ids: tuple[str, ...] = (),
    store: str = "default",
    rank: int | None = None,
    operator_node_id: str,
    predicate_id: str,
) -> RepairState:
    if any(item.item_id == item_id for item in state.items):
        raise RepairStateError(f"duplicate item id: {item_id!r}")
    next_rank = (
        rank
        if rank is not None
        else (max((item.rank for item in state.items), default=-1) + 1)
    )
    new_item = RepairStateItem(
        item_id=item_id,
        text=text,
        source_event_ids=tuple(source_event_ids),
        store=store,
        provenance_hash=_provenance_hash(item_id, text, tuple(source_event_ids)),
        rank=next_rank,
        disposition="active",
    )
    return _transition(
        state,
        items=state.items + (new_item,),
        matched_item_ids=(item_id,),
        action="add",
        operator_node_id=operator_node_id,
        predicate_id=predicate_id,
        logical_cost=1,
    )
