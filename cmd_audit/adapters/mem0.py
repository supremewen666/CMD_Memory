"""Mem0 adapter: intercepts mem0 ``add()`` and ``search()`` and runs the adapter replay portfolio."""

from __future__ import annotations

from cmd_audit.core.models import MemoryItem, ProbeCase
from cmd_audit.repair import UnsupportedActionError
from cmd_audit.replays import AgentGenerate, EvidenceScorer, ReplayResult

from ._replay_skeleton import run_adapter_replay_portfolio
from ._shared import intercept_search_side, intercept_write_side
from .base import (
    AdapterRepairMixin,
    Mem0Trace,
    RepairAction,
    ReplayName,
    SandboxViolationError,
    StoreChecksum,
)


class Mem0Adapter(AdapterRepairMixin):
    """Intercepts mem0 ``add()`` and ``search()`` at two cut points.

    All mutations are in-memory sandboxed variants.  The original store is
    never written to — verifiable via ``get_store_snapshot()`` checksum
    comparison.
    """

    def __init__(self, trace: Mem0Trace, gold_evidence, extracted_memory, raw_events):
        self._trace = trace
        self._gold_evidence = gold_evidence
        self._extracted_memory = extracted_memory
        self._raw_events = raw_events
        self._pre_checksum = trace.store_checksum

    # ── Domain-specific accessors ──────────────────────────────────────

    @property
    def original_add_inputs(self) -> list[str]:
        """Facts that mem0's ``add()`` was originally called with."""
        return list(self._trace.add_inputs)

    @property
    def original_search_query(self) -> str:
        """Query that mem0's ``search()`` was originally called with."""
        return self._trace.search_query

    @property
    def original_search_results(self) -> list[MemoryItem]:
        """MemoryItems that mem0's ``search()`` originally returned."""
        return list(self._trace.search_results)

    # ── Standard adapter interface (used by shared replay skeleton) ────

    @property
    def original_inputs(self) -> list[str]:
        return self.original_add_inputs

    @property
    def original_query(self) -> str:
        return self.original_search_query

    @property
    def original_results(self) -> list[MemoryItem]:
        return self.original_search_results

    # ── Cut Point A: add() interception ────────────────────────────────

    def intercept_add(
        self, case_id: str, original_facts: list[str], replay: ReplayName
    ) -> list[str]:
        """Return oracle facts for *replay*, or *original_facts* for passthrough."""
        return intercept_write_side(
            replay, original_facts, self._gold_evidence,
            self._extracted_memory, self._trace.search_results,
        )

    intercept_write = intercept_add

    # ── Cut Point B: search() interception ─────────────────────────────

    def intercept_search(
        self,
        case_id: str,
        original_query: str,
        original_results: list[MemoryItem],
        replay: ReplayName,
    ) -> list[MemoryItem]:
        """Return oracle results for *replay*, or *original_results* for passthrough."""
        return intercept_search_side(
            replay, original_results, self._gold_evidence, self._extracted_memory,
        )

    # ── Sandbox ────────────────────────────────────────────────────────

    def get_store_snapshot(self) -> StoreChecksum:
        import hashlib

        sorted_facts = sorted(self._trace.add_inputs)
        current = hashlib.sha256("|".join(sorted_facts).encode()).hexdigest()
        return StoreChecksum(
            checksum=current,
            item_count=len(self._trace.add_inputs),
        )

    # ── Issue 0020-B: Repair Action Support ────────────────────────────

    supported_actions = ("append", "replace")

    def apply_repair(self, action: RepairAction) -> str:
        """Apply a RepairAction to mem0's store (sandboxed in-memory)."""
        self._validate_action_type(action)
        pre = self.get_store_snapshot()

        if action.action_type == "append":
            new_inputs = list(self._trace.add_inputs) + [action.content]
            # Mem0Trace is frozen — create in-memory mutation tracker
            object.__setattr__(
                self._trace, "add_inputs", tuple(new_inputs)
            )
        elif action.action_type == "replace":
            if action.target_item_id is None:
                raise ValueError("replace requires target_item_id")
            new_results = []
            replaced = False
            for item in self._trace.search_results:
                if item.memory_id == action.target_item_id:
                    new_results.append(
                        item.__class__(
                            memory_id=item.memory_id,
                            text=action.content,
                            source_event_ids=item.source_event_ids,
                            store=item.store,
                            is_graph_expanded=item.is_graph_expanded,
                            provenance=item.provenance,
                        )
                    )
                    replaced = True
                else:
                    new_results.append(item)
            if not replaced:
                raise ValueError(
                    f"target_item_id {action.target_item_id!r} not found in store"
                )
            object.__setattr__(
                self._trace, "search_results", tuple(new_results)
            )

        post = self.get_store_snapshot()
        if post.checksum == pre.checksum:
            raise SandboxViolationError(
                f"Store checksum unchanged after repair for case {self._trace.case_id!r}"
            )
        return f"mem0 {action.action_type}: {action.target_item_id or 'new'} -> {action.target_store}"

    def _validate_action_type(self, action: RepairAction) -> None:
        if action.action_type not in self.supported_actions:
            raise UnsupportedActionError(
                f"Action type {action.action_type!r} not supported; "
                f"mem0 supports {self.supported_actions}"
            )

    def verify_sandbox(self) -> None:
        """Raise ``SandboxViolationError`` if the store checksum changed."""
        current = self.get_store_snapshot()
        if current.checksum != self._pre_checksum:
            raise SandboxViolationError(
                f"Store checksum mismatch for case {self._trace.case_id!r}: "
                f"pre={self._pre_checksum!r} post={current.checksum!r}"
            )


def run_mem0_replay_portfolio(
    case: ProbeCase,
    adapter: Mem0Adapter,
    *,
    tracker: object | None = None,
    scorer: EvidenceScorer | None = None,
    agent_generate: AgentGenerate | None = None,
) -> tuple[ReplayResult, ...]:
    """Run 6 adapter-intercepted replays + 4 V1 passthrough replays."""
    return run_adapter_replay_portfolio(
        case,
        adapter,
        tracker=tracker,
        scorer=scorer,
        agent_generate=agent_generate,
    )
