"""Root-bound answer rendering for ECC before/after causal contrasts.

The renderer has one prompt scaffold.  A process-fault subtype changes only
the deterministic memory view derived from the corresponding pipeline flag.
It never reads benchmark targets or scorer output.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from cmd_audit.core.state_codec import content_sha256
from experiments.experiment_runner_common import AGENT_SYSTEM_PROMPT
from experiments.model_context_budget import BudgetedContext, ModelContextBudget


ANSWER_RENDERER_SCHEMA = "cmd-ecc-answer-state-renderer-v1"
MEMORY_HEADING = "Retrieved memory"
PROCESS_FAULT_SUBTYPES = ("retrieval", "injection", "granularity", "safety")
OPERATOR_SEMANTICS = {
    "retrieval": "retrieval-outage-empty-result-v1",
    "injection": "context-injection-placeholder-v1",
    "granularity": "memory-detail-coarsening-v1",
    "safety": "evidence-safety-redaction-v1",
}


@dataclass(frozen=True)
class CausalRenderedContext:
    """One fully audited state-to-context rendering."""

    budgeted: BudgetedContext
    process_fault_subtype: str
    operator_semantics: str
    state_root: str
    source_memory_order: tuple[str, ...]
    active_memory_order: tuple[str, ...]
    rendered_items_sha256: str
    context_sha256: str


def _available_text(case: object) -> dict[str, str]:
    raw = getattr(case, "raw")
    extracted = raw.get("extracted_memory")
    if not isinstance(extracted, list):
        raise ValueError("benchmark runtime memory view is invalid")
    available = {
        str(item["memory_id"]): str(item["text"])
        for item in extracted
        if isinstance(item, Mapping)
        and isinstance(item.get("memory_id"), str)
        and isinstance(item.get("text"), str)
    }
    return available


def _coarsen(text: str) -> str:
    words = " ".join(text.split()).split(" ")
    prefix = " ".join(words[:8]).strip()
    return f"[coarse-granularity] {prefix or '(empty-memory)'}"


def _typed_items(
    *,
    subtype: str,
    fault_active: bool,
    ordered_items: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...]:
    if not fault_active:
        return ordered_items
    if subtype == "retrieval":
        return ()
    if subtype == "injection":
        return tuple(
            (memory_id, "[context-injection-missing]")
            for memory_id, _text in ordered_items
        )
    if subtype == "granularity":
        return tuple(
            (memory_id, _coarsen(text)) for memory_id, text in ordered_items
        )
    if subtype == "safety":
        return tuple(
            (memory_id, "[evidence-withheld-by-safety-gate]")
            for memory_id, _text in ordered_items
        )
    raise ValueError(f"unknown process-fault subtype: {subtype}")


def render_causal_state(
    *,
    case: object,
    state: Mapping[str, object],
    state_root: str,
    process_fault_subtype: str,
    query: str,
    budget: ModelContextBudget,
) -> CausalRenderedContext:
    """Render one before/after state through the shared answer scaffold."""

    if process_fault_subtype not in PROCESS_FAULT_SUBTYPES:
        raise ValueError("causal answer state has an unknown process-fault subtype")
    expected_root = content_sha256(dict(state), ensure_ascii=False, allow_nan=False)
    if state_root != expected_root:
        raise ValueError("causal answer state does not match its bound root")
    pipeline = state.get("pipeline")
    memories = state.get("memories")
    memory_order = state.get("memory_order")
    quarantine = state.get("quarantine")
    if (
        not isinstance(pipeline, Mapping)
        or set(pipeline) != set(PROCESS_FAULT_SUBTYPES)
        or any(not isinstance(value, bool) for value in pipeline.values())
        or not isinstance(memories, Mapping)
        or not isinstance(memory_order, list)
        or any(not isinstance(memory_id, str) for memory_id in memory_order)
        or len(set(memory_order)) != len(memory_order)
        or set(memory_order) != set(memories)
        or not isinstance(quarantine, list)
    ):
        raise ValueError("causal answer state is not closed or ordered")
    available = _available_text(case)
    unknown = set(memory_order) - set(available)
    if unknown:
        raise ValueError(
            "causal state references memory IDs absent from benchmark runtime view: "
            f"{sorted(unknown)!r}"
        )
    quarantined = set(quarantine)
    active_order = tuple(
        memory_id
        for memory_id in memory_order
        if isinstance(memories[memory_id], Mapping)
        and memories[memory_id].get("active") is True
        and memory_id not in quarantined
    )
    ordered_items = tuple((memory_id, available[memory_id]) for memory_id in active_order)
    rendered_items = _typed_items(
        subtype=process_fault_subtype,
        fault_active=pipeline[process_fault_subtype] is False,
        ordered_items=ordered_items,
    )
    fitted = budget.fit_memory_items(
        query=query,
        items=rendered_items,
        system=AGENT_SYSTEM_PROMPT,
        heading=MEMORY_HEADING,
    )
    return CausalRenderedContext(
        budgeted=fitted,
        process_fault_subtype=process_fault_subtype,
        operator_semantics=OPERATOR_SEMANTICS[process_fault_subtype],
        state_root=state_root,
        source_memory_order=tuple(memory_order),
        active_memory_order=active_order,
        rendered_items_sha256=content_sha256(
            [list(item) for item in rendered_items],
            ensure_ascii=False,
            allow_nan=False,
        ),
        context_sha256=content_sha256(fitted.context),
    )


__all__ = [
    "ANSWER_RENDERER_SCHEMA",
    "CausalRenderedContext",
    "MEMORY_HEADING",
    "OPERATOR_SEMANTICS",
    "PROCESS_FAULT_SUBTYPES",
    "render_causal_state",
]
