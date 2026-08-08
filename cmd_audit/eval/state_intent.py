"""Route A E-1: runtime/gold separation boundary.

BUILD SPEC §3.1 defines a public ``RuntimeRepairCase`` that synthesized repair
programs may read, and a forbidden-field list they may not. ``ProbeCase``
cannot be passed to synthesized code because it carries gold evidence, the
gold answer, and the injector's perturbation label in the same object as the
runtime memory items. This module is the explicit conversion boundary.

The hidden intent manifest (§3.3) lives here too, adjacent to the boundary it
is sealed behind, so that the one type synthesized code may read and the one
type only the evaluator may read are visibly separated in one file.
"""

from dataclasses import dataclass
from typing import Any

__all__ = [
    "FORBIDDEN_RUNTIME_FIELDS",
    "TEMPLATE_HINT_MARKERS",
    "STATE_INTENT_SCHEMA_VERSION",
    "RuntimeSeparationError",
    "RuntimeMemoryItem",
    "RuntimeEvent",
    "RuntimeRepairCase",
    "RequiredItemIntent",
    "PerturbationIntent",
    "HiddenStateIntent",
    "runtime_case_from_probe_case",
    "runtime_case_to_mapping",
]

STATE_INTENT_SCHEMA_VERSION = "state-intent-v1"


# BUILD SPEC §3.1 forbidden-field list.
FORBIDDEN_RUNTIME_FIELDS = (
    "gold_evidence",
    "gold_answer",
    "perturbation_label",
    "source_memory_id",
    "required_phrases",
    "disposition",
    "target_item_id",
    "passed_safety_filter",
    "split",
    "bucket",
)

# BUILD SPEC §5.3 injector template hints that must not survive into runtime
# item text. Present in injector v1 data (e.g. the literal "M_new:" prefix);
# a tier-3 dataset carrying any of them fails dataset validity.
TEMPLATE_HINT_MARKERS = (
    "M_new:",
    "M_old:",
    "target_item",
    "gold_item",
    "corrupted_item",
)


class RuntimeSeparationError(ValueError):
    """Raised when a forbidden value would cross the runtime boundary."""


@dataclass(frozen=True)
class RuntimeMemoryItem:
    """A memory item as synthesized code may observe it.

    ``retrieved`` records whether the recorded pipeline run actually surfaced
    this item. Items with ``retrieved=False`` are the candidate pool: visible to
    a repair operator as things it may pull in, but not part of the context the
    generator saw. Collapsing the two is what makes a retrieval miss
    inexpressible and gold free-present.
    """

    item_id: str
    text: str
    source_event_ids: tuple[str, ...] = ()
    store: str = "default"
    rank: int = 0
    retrieved: bool = True


@dataclass(frozen=True)
class RuntimeEvent:
    event_id: str
    text: str


@dataclass(frozen=True)
class RuntimeRepairCase:
    """BUILD SPEC §3.1. Contains no gold, label, or intent field."""

    case_id: str
    family_id: str
    query: str
    items: tuple[RuntimeMemoryItem, ...]
    raw_events: tuple[RuntimeEvent, ...]
    token_budget: int
    runtime_surface: str = "route-a-runtime-v1"


@dataclass(frozen=True)
class RequiredItemIntent:
    """An item the repaired state must still carry (BUILD SPEC §3.3)."""

    source_memory_id: str
    required_phrases: tuple[str, ...]
    allowed_dispositions: tuple[str, ...] = ("active",)


@dataclass(frozen=True)
class PerturbationIntent:
    """An injected fault and the dispositions that legally resolve it."""

    target_item_id: str
    allowed_resolutions: tuple[str, ...]
    replacement_item_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class HiddenStateIntent:
    """Sealed manifest. Readable by the evaluator and post-outcome analysis only.

    Constructed from gold evidence and injector records; never passed to a
    synthesized program and never serialized onto the runtime surface.
    """

    case_id: str
    family_id: str
    required_items: tuple[RequiredItemIntent, ...]
    perturbations: tuple[PerturbationIntent, ...]
    protected_item_ids: tuple[str, ...]
    allowed_added_item_ids: tuple[str, ...]
    required_provenance_hashes: tuple[tuple[str, str], ...]
    token_budget: int
    null_case: bool
    schema_version: str = STATE_INTENT_SCHEMA_VERSION


def runtime_case_from_probe_case(
    case: Any,
    *,
    token_budget: int,
    family_id: str | None = None,
    reject_template_hints: bool = False,
) -> RuntimeRepairCase:
    """Project a ``ProbeCase`` onto the runtime surface, dropping gold fields.

    ``reject_template_hints`` enforces §5.3 and is off for development data
    (injector v1 wrote "M_new:" into item text, so burned dev cases would all
    fail); it is on for tier-3 dataset validation.
    """
    recall_order = {
        memory_id: position
        for position, memory_id in enumerate(
            case.primary_baseline.retrieved_memory_ids
        )
    }
    # A recalled item the safety layer redacted never reached the generator's
    # context, so it is not visible even though it was retrieved.
    redacted = bool(getattr(case, "safety_filter_blocked", False))
    pool_offset = len(recall_order)
    items = tuple(
        RuntimeMemoryItem(
            item_id=item.memory_id,
            text=item.text,
            source_event_ids=tuple(item.source_event_ids),
            store=item.store,
            rank=(
                recall_order[item.memory_id]
                if item.memory_id in recall_order
                else pool_offset + position
            ),
            retrieved=(
                item.memory_id in recall_order
                and not (redacted and item.passed_safety_filter)
            ),
        )
        for position, item in enumerate(case.extracted_memory)
    )
    if reject_template_hints:
        for item in items:
            for marker in TEMPLATE_HINT_MARKERS:
                if marker in item.text:
                    raise RuntimeSeparationError(
                        f"{case.case_id}: item {item.item_id} carries injector "
                        f"template hint {marker!r}"
                    )
    return RuntimeRepairCase(
        case_id=case.case_id,
        family_id=family_id if family_id is not None else case.case_id,
        query=case.query,
        items=items,
        raw_events=tuple(
            RuntimeEvent(event_id=event.event_id, text=event.text)
            for event in case.raw_events
        ),
        token_budget=int(token_budget),
    )


def runtime_case_to_mapping(case: RuntimeRepairCase) -> dict[str, Any]:
    """Serialize for the runtime JSONL surface."""
    return {
        "case_id": case.case_id,
        "family_id": case.family_id,
        "query": case.query,
        "items": [
            {
                "item_id": item.item_id,
                "text": item.text,
                "source_event_ids": list(item.source_event_ids),
                "store": item.store,
                "rank": item.rank,
            }
            for item in case.items
        ],
        "raw_events": [
            {"event_id": event.event_id, "text": event.text}
            for event in case.raw_events
        ],
        "token_budget": case.token_budget,
        "runtime_surface": case.runtime_surface,
    }
