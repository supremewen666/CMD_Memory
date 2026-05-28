"""Letta adapter: intercepts ``core_write``, ``archival_store``, and ``recall`` and runs the adapter replay portfolio."""

from __future__ import annotations

from cmd_audit.core.models import MemoryItem, ProbeCase
from cmd_audit.repair import UnsupportedActionError
from cmd_audit.replays import AgentGenerate, EvidenceScorer, ReplayResult

from ._replay_skeleton import run_adapter_replay_portfolio
from ._shared import intercept_search_side, intercept_write_side
from .base import (
    AdapterRepairMixin,
    LettaTrace,
    RepairAction,
    ReplayName,
    SandboxViolationError,
    StoreChecksum,
)


class LettaAdapter(AdapterRepairMixin):
    """Intercepts Letta core/archival/recall operations at three cut points.

    All mutations are in-memory sandboxed variants.  The original store is
    never written to — verifiable via ``get_store_snapshot()`` checksum
    comparison.
    """

    def __init__(self, trace: LettaTrace, gold_evidence, extracted_memory, raw_events):
        self._trace = trace
        self._gold_evidence = gold_evidence
        self._extracted_memory = extracted_memory
        self._raw_events = raw_events
        self._pre_checksum = trace.store_checksum

    # ── Domain-specific accessors ──────────────────────────────────────

    @property
    def original_core_blocks(self) -> list[str]:
        """Blocks originally written to Letta's core memory."""
        return list(self._trace.core_blocks)

    @property
    def original_archival_blocks(self) -> list[str]:
        """Entries originally stored in Letta's archival memory."""
        return list(self._trace.archival_blocks)

    @property
    def original_recall_query(self) -> str:
        """Query that Letta's ``recall()`` was originally called with."""
        return self._trace.recall_query

    @property
    def original_recall_results(self) -> list[MemoryItem]:
        """MemoryItems that Letta's ``recall()`` originally returned."""
        return list(self._trace.recall_results)

    # ── Standard adapter interface (used by shared replay skeleton) ────

    @property
    def original_inputs(self) -> list[str]:
        return self.original_core_blocks

    @property
    def original_query(self) -> str:
        return self.original_recall_query

    @property
    def original_results(self) -> list[MemoryItem]:
        return self.original_recall_results

    # ── Cut Point A: core_write interception ───────────────────────────

    def intercept_core_write(
        self, case_id: str, original_blocks: list[str], replay: ReplayName
    ) -> list[str]:
        """Return oracle blocks for *replay*, or *original_blocks* for passthrough."""
        return intercept_write_side(
            replay, original_blocks, self._gold_evidence,
            self._extracted_memory, self._trace.recall_results,
        )

    intercept_write = intercept_core_write

    # ── Cut Point B: archival_store interception ───────────────────────

    def intercept_archival_store(
        self, case_id: str, original_entries: list[str], replay: ReplayName
    ) -> list[str]:
        """Return oracle entries for *replay*, or *original_entries* for passthrough."""
        return intercept_write_side(
            replay, original_entries, self._gold_evidence,
            self._extracted_memory, self._trace.recall_results,
        )

    # ── Cut Point C: recall interception ───────────────────────────────

    def intercept_recall(
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

    intercept_search = intercept_recall

    # ── Sandbox ────────────────────────────────────────────────────────

    def get_store_snapshot(self) -> StoreChecksum:
        import hashlib

        sorted_facts = sorted(
            list(self._trace.core_blocks) + list(self._trace.archival_blocks)
        )
        current = hashlib.sha256("|".join(sorted_facts).encode()).hexdigest()
        return StoreChecksum(
            checksum=current,
            item_count=len(self._trace.core_blocks) + len(self._trace.archival_blocks),
        )

    # ── Issue 0020-B: Repair Action Support ────────────────────────────

    supported_actions = ("append", "replace", "relocate", "update_routing")

    def apply_repair(self, action: RepairAction) -> str:
        """Apply a RepairAction to Letta's store (sandboxed in-memory)."""
        self._validate_action_type(action)
        pre = self.get_store_snapshot()

        if action.action_type == "append":
            new_blocks = list(self._trace.core_blocks) + [action.content]
            object.__setattr__(self._trace, "core_blocks", tuple(new_blocks))
        elif action.action_type == "replace":
            if action.target_item_id is None:
                raise ValueError("replace requires target_item_id")
            new_results = []
            replaced = False
            for item in self._trace.recall_results:
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
                    f"target_item_id {action.target_item_id!r} not found"
                )
            object.__setattr__(
                self._trace, "recall_results", tuple(new_results)
            )
        elif action.action_type == "relocate":
            # Simulate moving content between tiers
            new_blocks = list(self._trace.core_blocks) + [action.content]
            object.__setattr__(self._trace, "core_blocks", tuple(new_blocks))
        elif action.action_type == "update_routing":
            # Simulate routing config change (no-op in sandbox)
            pass

        post = self.get_store_snapshot()
        if post.checksum == pre.checksum and action.action_type != "update_routing":
            raise SandboxViolationError(
                f"Store checksum unchanged after repair for case {self._trace.case_id!r}"
            )
        return f"letta {action.action_type}: {action.target_item_id or 'new'} -> {action.target_store}"

    def _validate_action_type(self, action: RepairAction) -> None:
        if action.action_type not in self.supported_actions:
            raise UnsupportedActionError(
                f"Action type {action.action_type!r} not supported; "
                f"letta supports {self.supported_actions}"
            )

    def verify_sandbox(self) -> None:
        """Raise ``SandboxViolationError`` if the store checksum changed."""
        current = self.get_store_snapshot()
        if current.checksum != self._pre_checksum:
            raise SandboxViolationError(
                f"Store checksum mismatch for case {self._trace.case_id!r}: "
                f"pre={self._pre_checksum!r} post={current.checksum!r}"
            )


def run_letta_replay_portfolio(
    case: ProbeCase,
    adapter: LettaAdapter,
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
