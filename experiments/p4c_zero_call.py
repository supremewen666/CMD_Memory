"""Deterministic, zero-model-call substrate for the P4C ECC live ABI."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Sequence

from cmd_audit.core.state_codec import atomic_json_write, content_sha256
from cmd_audit.repair.ecc import EccRepairReceipt, EccSyndrome, MemAuditEccAdapter
from cmd_audit.repair.incident_triage import IncidentMechanism
from experiments.p4c_ecc_runner import P4cEccCase, P4cEccRunner


_STATE_FIELDS = frozenset(
    {"pipeline", "memories", "lineage", "quarantine", "protected_ids"}
)


def _state_root(state: Mapping[str, object]) -> str:
    return content_sha256(dict(state), ensure_ascii=False, allow_nan=False)


def _leaves(value: object, path: tuple[object, ...] = ()) -> dict[tuple[object, ...], object]:
    if isinstance(value, Mapping):
        result: dict[tuple[object, ...], object] = {}
        for key, nested in value.items():
            result.update(_leaves(nested, (*path, key)))
        return result
    if isinstance(value, list):
        result = {}
        for index, nested in enumerate(value):
            result.update(_leaves(nested, (*path, index)))
        return result
    return {path: value}


class StructuralMemoryStore:
    """Copy-on-write structural memory state with registered repair programs."""

    def __init__(
        self,
        *,
        state: Mapping[str, object],
        operators: Mapping[str, Mapping[str, object]],
    ) -> None:
        if set(state) != _STATE_FIELDS:
            raise ValueError("zero-call structural state is not closed")
        if not operators:
            raise ValueError("zero-call structural operators are required")
        self._state = deepcopy(dict(state))
        self._operators = deepcopy(dict(operators))
        self._shadow: dict[str, object] | None = None
        self._validate_state(self._state)

    @property
    def state(self) -> Mapping[str, object]:
        return MappingProxyType(deepcopy(self._state))

    def snapshot_root(self) -> str:
        current = self._shadow if self._shadow is not None else self._state
        return _state_root(current)

    def apply_shadow(
        self, syndrome: EccSyndrome, selected_skill_revision_id: str
    ) -> None:
        if self._shadow is not None:
            raise RuntimeError("zero-call shadow transition is already open")
        try:
            operator = self._operators[selected_skill_revision_id]
        except KeyError as exc:
            raise ValueError("selected zero-call operator is not registered") from exc
        if set(operator) not in ({"kind"}, {"kind", "variant"}):
            raise ValueError("zero-call operator program is not closed")
        variant = operator.get("variant", "repair")
        if variant not in {"repair", "unsafe_protected_mutation"}:
            raise ValueError("unknown zero-call operator variant")
        shadow = deepcopy(self._state)
        if operator["kind"] == "pipeline_patch":
            if syndrome.process_fault_subtype is None:
                raise ValueError("pipeline_patch requires a process-fault syndrome")
            pipeline = shadow["pipeline"]
            assert isinstance(pipeline, dict)
            pipeline[syndrome.process_fault_subtype.value] = True
        elif operator["kind"] == "supersede_lineage":
            old_id = syndrome.superseded_memory_id
            new_id = syndrome.superseding_memory_id
            if not old_id or not new_id:
                raise ValueError("supersede_lineage requires a state-drift syndrome")
            memories = shadow["memories"]
            lineage = shadow["lineage"]
            assert isinstance(memories, dict) and isinstance(lineage, list)
            if old_id not in memories or new_id not in memories:
                raise ValueError("state-drift lineage references unknown memories")
            memories[old_id]["active"] = False
            memories[new_id]["active"] = True
            edge = [old_id, new_id]
            if edge not in lineage:
                lineage.append(edge)
        elif operator["kind"] == "quarantine_poison":
            memories = shadow["memories"]
            quarantine = shadow["quarantine"]
            assert isinstance(memories, dict) and isinstance(quarantine, list)
            if any(suspect_id not in memories for suspect_id in syndrome.suspect_ids):
                raise ValueError("poison syndrome references unknown memories")
            for suspect_id in syndrome.suspect_ids:
                memories[suspect_id]["active"] = False
                if suspect_id not in quarantine:
                    quarantine.append(suspect_id)
        else:
            raise ValueError("unknown zero-call operator kind")
        if variant == "unsafe_protected_mutation":
            protected = shadow["protected_ids"]
            memories = shadow["memories"]
            assert isinstance(protected, list) and isinstance(memories, dict)
            if not protected:
                raise ValueError("unsafe calibration variant requires a protected memory")
            memories[protected[0]]["active"] = False
        self._validate_state(shadow)
        self._shadow = shadow

    def commit_shadow(self) -> None:
        if self._shadow is None:
            raise RuntimeError("zero-call commit requires an open shadow transition")
        self._state = self._shadow
        self._shadow = None

    def rollback_shadow(self, before_root: str) -> None:
        if self._shadow is None:
            raise RuntimeError("zero-call rollback requires an open shadow transition")
        self._shadow = None
        if self.snapshot_root() != before_root:
            raise ValueError("zero-call rollback did not restore before_root")

    def parity_state(self) -> Mapping[str, object]:
        if self._shadow is None:
            raise RuntimeError("ECC evaluation requires an open shadow transition")
        return MappingProxyType(deepcopy(self._shadow))

    def committed_state(self) -> Mapping[str, object]:
        return MappingProxyType(deepcopy(self._state))

    @staticmethod
    def _validate_state(state: Mapping[str, object]) -> None:
        pipeline = state.get("pipeline")
        if not isinstance(pipeline, Mapping) or set(pipeline) != {
            "retrieval", "injection", "granularity", "safety"
        } or any(not isinstance(value, bool) for value in pipeline.values()):
            raise ValueError("zero-call pipeline state is invalid")
        memories = state.get("memories")
        if not isinstance(memories, Mapping):
            raise ValueError("zero-call memories must be a mapping")
        if any(
            not isinstance(memory_id, str)
            or not memory_id
            or not isinstance(record, Mapping)
            or "active" not in record
            or set(record) - {"active", "content_sha256", "source_root"}
            or not isinstance(record["active"], bool)
            or any(
                not isinstance(record.get(name), str)
                or len(str(record[name])) != 64
                or any(char not in "0123456789abcdef" for char in str(record[name]))
                for name in ("content_sha256", "source_root")
                if name in record
            )
            for memory_id, record in memories.items()
        ):
            raise ValueError("zero-call memory records are invalid")
        for name in ("lineage", "quarantine", "protected_ids"):
            value = state.get(name)
            if not isinstance(value, list):
                raise ValueError(f"zero-call {name} must be a list")
        quarantine = state["quarantine"]
        protected = state["protected_ids"]
        if any(not isinstance(item, str) or item not in memories for item in quarantine):
            raise ValueError("zero-call quarantine references unknown memories")
        if any(not isinstance(item, str) or item not in memories for item in protected):
            raise ValueError("zero-call protected_ids references unknown memories")
        if len(set(quarantine)) != len(quarantine) or len(set(protected)) != len(protected):
            raise ValueError("zero-call state ID sets must be unique")


class StructuralEccEvaluator:
    """Evaluate only state structure; it has no answer, label, or replay seam."""

    def __init__(self, store: StructuralMemoryStore) -> None:
        if not isinstance(store, StructuralMemoryStore):
            raise TypeError("structural ECC evaluator requires its shadow store")
        self.store = store

    def evaluate_ecc(
        self,
        syndrome: EccSyndrome,
        *,
        before_root: str,
        shadow_root: str,
    ) -> dict[str, object]:
        state = self.store.parity_state()
        before = self.store.committed_state()
        if _state_root(state) != shadow_root or _state_root(before) != before_root:
            raise ValueError("zero-call ECC root binding is invalid")
        pipeline = state["pipeline"]
        assert isinstance(pipeline, Mapping)
        memories = state["memories"]
        lineage = state["lineage"]
        assert isinstance(memories, Mapping) and isinstance(lineage, list)
        if syndrome.mechanism is IncidentMechanism.PROCESS_FAULT:
            resolved = bool(
                syndrome.process_fault_subtype is not None
                and pipeline.get(syndrome.process_fault_subtype.value) is True
            )
        elif syndrome.mechanism is IncidentMechanism.STATE_DRIFT:
            old_id = syndrome.superseded_memory_id
            new_id = syndrome.superseding_memory_id
            resolved = bool(
                old_id in memories
                and new_id in memories
                and memories[old_id]["active"] is False
                and memories[new_id]["active"] is True
                and [old_id, new_id] in lineage
            )
        else:
            quarantine = state["quarantine"]
            assert isinstance(quarantine, list)
            resolved = bool(
                syndrome.suspect_ids
                and all(
                    suspect_id in quarantine
                    and memories[suspect_id]["active"] is False
                    for suspect_id in syndrome.suspect_ids
                )
            )
        quarantine = state["quarantine"]
        protected = state["protected_ids"]
        assert isinstance(quarantine, list) and isinstance(protected, list)
        lineage_valid = all(
            isinstance(edge, list)
            and len(edge) == 2
            and edge[0] in memories
            and edge[1] in memories
            and edge[0] != edge[1]
            and memories[edge[0]]["active"] is False
            for edge in lineage
        )
        quarantine_valid = all(
            memory_id in memories and memories[memory_id]["active"] is False
            for memory_id in quarantine
        )
        invariants_passed = bool(lineage_valid and quarantine_valid)
        safety_violation = any(
            memory_id in quarantine or memories[memory_id]["active"] is False
            for memory_id in protected
        )
        before_leaves = _leaves(before)
        after_leaves = _leaves(state)
        changed = sum(
            before_leaves.get(path) != after_leaves.get(path)
            for path in set(before_leaves) | set(after_leaves)
        )
        locality_cost = changed / max(1, len(before_leaves))
        return {
            "resolved_syndrome": resolved,
            "invariants_passed": invariants_passed,
            "safety_violation": safety_violation,
            "locality_cost": locality_cost,
            "recurrence_after_commit": False,
            "provenance": {"checker": "structural-zero-call-v1"},
        }


@dataclass(frozen=True)
class P4cZeroCallScenario:
    """One frozen incident case plus its deterministic structural substrate."""

    case: P4cEccCase
    state: Mapping[str, object]
    operators: Mapping[str, Mapping[str, object]]

    def __post_init__(self) -> None:
        if not isinstance(self.case, P4cEccCase):
            raise TypeError("zero-call scenario requires a P4cEccCase")
        store = StructuralMemoryStore(state=self.state, operators=self.operators)
        if store.snapshot_root() != self.case.observation["state_root"]:
            raise ValueError("zero-call scenario state root does not bind its case")


class P4cZeroCallSuite:
    """Run P4C mechanism cases without model, answer, or replay calls."""

    def __init__(
        self,
        scenarios: Sequence[P4cZeroCallScenario],
        *,
        output_dir: Path,
        router: object,
    ) -> None:
        if not scenarios:
            raise ValueError("P4C-0 requires at least one scenario")
        if any(not isinstance(row, P4cZeroCallScenario) for row in scenarios):
            raise TypeError("P4C-0 scenarios must be typed")
        self.scenarios = tuple(scenarios)
        self.output_dir = Path(output_dir)
        self.router = router

    def run(self) -> dict[str, object]:
        stores = {
            scenario.case.case_id: StructuralMemoryStore(
                state=scenario.state, operators=scenario.operators
            )
            for scenario in self.scenarios
        }
        runtime = P4cEccRunner(
            tuple(scenario.case for scenario in self.scenarios),
            output_dir=self.output_dir,
            router=self.router,
            store_factory=lambda case: stores[case.case_id],
            evaluator_factory=lambda case: StructuralEccEvaluator(
                stores[case.case_id]
            ),
            run_mode="fresh",
        ).run()
        receipts = [
            EccRepairReceipt.from_mapping(json.loads(line))
            for line in (self.output_dir / "repair_receipts.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line
        ]
        if len(receipts) != len(self.scenarios):
            raise ValueError("P4C-0 runtime did not emit exactly one receipt per case")
        count = len(receipts)
        mechanisms: dict[str, int] = {}
        adapter = MemAuditEccAdapter()
        for scenario in self.scenarios:
            mechanism = adapter.decode(scenario.case.observation).mechanism.value
            mechanisms[mechanism] = mechanisms.get(mechanism, 0) + 1
        report: dict[str, object] = {
            "schema_version": "cmd-p4c-zero-call-report-v1",
            "status": "success",
            "run_manifest_sha256": runtime["run_manifest_sha256"],
            "receipt_root": runtime["receipt_root"],
            "case_count": count,
            "mechanism_counts": mechanisms,
            "model_call_count": 0,
            "external_call_count": 0,
            "runtime_uses_gold": False,
            "runtime_uses_labels": False,
            "same_trace_answer_replay": False,
            "syndrome_resolution_rate": sum(
                int(receipt.resolved_syndrome) for receipt in receipts
            ) / count,
            "invariant_pass_rate": sum(
                int(receipt.invariants_passed) for receipt in receipts
            ) / count,
            "commit_rate": sum(int(receipt.committed) for receipt in receipts) / count,
            "rollback_rate": sum(int(receipt.rolled_back) for receipt in receipts) / count,
            "safety_violation_rate": sum(
                int(receipt.safety_violation) for receipt in receipts
            ) / count,
            "mean_locality_cost": sum(
                receipt.locality_cost for receipt in receipts
            ) / count,
            "recurrence_rate": sum(
                int(receipt.recurrence_after_commit) for receipt in receipts
            ) / count,
            "final_state_roots": {
                case_id: store.snapshot_root() for case_id, store in stores.items()
            },
            "claim_scope": "post-detection_ecc_mechanism_loop_only",
        }
        atomic_json_write(
            self.output_dir / "zero_call_report.json",
            report,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            trailing_newline=True,
        )
        return report


__all__ = [
    "P4cZeroCallScenario",
    "P4cZeroCallSuite",
    "StructuralEccEvaluator",
    "StructuralMemoryStore",
]
