"""Shared types for CMD-Skill Adapter implementations."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Literal

from cmd_audit.core.models import MemoryItem

# ── Replay name type ────────────────────────────────────────────────────

ReplayName = Literal[
    "oracle_write",
    "oracle_compression",
    "verbatim_event_oracle",
    "oracle_retrieval",
    "injection_oracle",
    "evidence_given_reasoning",
    "oracle_route",
    "oracle_granularity",
    "graph_off",
    "safety_off",
]

# ── Store snapshot ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class StoreChecksum:
    checksum: str
    item_count: int


# ── Sandbox violation ───────────────────────────────────────────────────


class SandboxViolationError(RuntimeError):
    """Raised when an adapter replay attempts to mutate the backing store."""


# ── Recorded mem0 trace ─────────────────────────────────────────────────


@dataclass(frozen=True)
class Mem0Trace:
    """Pre-recorded mem0 operations for one probe case.

    Stores what mem0's ``add()`` and ``search()`` returned during the
    original failed run so the adapter can replay without a live instance.
    """

    case_id: str
    add_inputs: tuple[str, ...]
    search_query: str
    search_results: tuple[MemoryItem, ...]
    store_checksum: str

    @classmethod
    def from_dict(cls, d: dict) -> "Mem0Trace":
        """Build a Mem0Trace from a JSON-compatible dict."""
        return cls(
            case_id=d["case_id"],
            add_inputs=tuple(d["add_inputs"]),
            search_query=d["search_query"],
            search_results=tuple(
                MemoryItem(
                    memory_id=item["memory_id"],
                    text=item["text"],
                    source_event_ids=tuple(item.get("source_event_ids", ())),
                    store=item.get("store", "default"),
                    is_graph_expanded=item.get("is_graph_expanded", False),
                )
                for item in d["search_results"]
            ),
            store_checksum=d["store_checksum"],
        )


def load_mem0_traces(path: str | Path) -> dict[str, Mem0Trace]:
    """Load mem0 traces from a JSON file, keyed by case_id."""
    with open(path, "r") as fh:
        raw = json.load(fh)
    return {item["case_id"]: Mem0Trace.from_dict(item) for item in raw}


# ── Letta trace (recorded-trace mode) ────────────────────────────────────


@dataclass(frozen=True)
class LettaTrace:
    """Pre-recorded Letta operations for one probe case.

    Captures Letta's tripartite memory model: core memory blocks (working
    memory), archival memory entries (long-term store), and recall results
    (retrieved context from core + archival).  The three-tier structure
    enables ``oracle_route`` to exercise tier selection in a way mem0's
    flat store cannot.
    """

    case_id: str
    core_blocks: tuple[str, ...]
    archival_blocks: tuple[str, ...]
    recall_query: str
    recall_results: tuple[MemoryItem, ...]
    store_checksum: str

    @classmethod
    def from_dict(cls, d: dict) -> "LettaTrace":
        """Build a LettaTrace from a JSON-compatible dict."""
        return cls(
            case_id=d["case_id"],
            core_blocks=tuple(d["core_blocks"]),
            archival_blocks=tuple(d.get("archival_blocks", ())),
            recall_query=d["recall_query"],
            recall_results=tuple(
                MemoryItem(
                    memory_id=item["memory_id"],
                    text=item["text"],
                    source_event_ids=tuple(item.get("source_event_ids", ())),
                    store=item.get("store", "default"),
                    is_graph_expanded=item.get("is_graph_expanded", False),
                )
                for item in d["recall_results"]
            ),
            store_checksum=d["store_checksum"],
        )


def load_letta_traces(path: str | Path) -> dict[str, LettaTrace]:
    """Load Letta traces from a JSON file, keyed by case_id."""
    with open(path, "r") as fh:
        raw = json.load(fh)
    return {item["case_id"]: LettaTrace.from_dict(item) for item in raw}


# ── Issue 0020-B: Adapter Repair Protocol ───────────────────────────────

from cmd_audit.repairs import RepairAction, UnsupportedActionError


class AdapterRepairMixin:
    """Mixin providing apply_repair interface for CMD-Skill Adapters.

    Adapters declare supported_actions and implement apply_repair.
    The LLM sees label + evidence_block + fm_context + supported_actions
    and autonomously selects action + fills parameters.
    """

    supported_actions: tuple[str, ...] = ()

    def apply_repair(self, action: RepairAction) -> str:
        """Apply a RepairAction to the backing store.

        Returns a confirmation message or raises SandboxViolationError
        if the store checksum changes unexpectedly.
        """
        raise NotImplementedError("apply_repair must be implemented by adapter subclass")

    def _validate_action_type(self, action: RepairAction) -> None:
        if action.action_type not in self.supported_actions:
            raise UnsupportedActionError(
                f"Action type {action.action_type!r} not supported; "
                f"adapter supports {self.supported_actions}"
            )
