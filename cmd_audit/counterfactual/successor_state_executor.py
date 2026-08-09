"""Zero-model, offline executor for the frozen successor-v3 graph."""

from __future__ import annotations

from dataclasses import dataclass

from cmd_audit.counterfactual.actionability import ActionMode
from cmd_audit.counterfactual.program_ir import ActionKind, ResourceBounds, REGISTERED_BOUNDS
from cmd_audit.counterfactual.relation_graph import FrozenRelationEdge, FrozenRelationGraph
from cmd_audit.counterfactual.repair_state import RepairState, apply_disposition
from cmd_audit.counterfactual.successor_program_ir import Action, Predicate, PredicateKind, Program, canonicalize, check_resource_bounds, iter_rules
from cmd_audit.eval.state_intent import FORBIDDEN_RUNTIME_FIELDS, RuntimeRepairCase, RuntimeSeparationError

__all__ = ["FrozenRelationEdge", "FrozenRelationGraph", "ExecutionResult", "ExecutionLimitError", "execute_program"]


class ExecutionLimitError(ValueError):
    pass


@dataclass(frozen=True)
class ExecutionResult:
    state: RepairState
    matched_item_count: int
    logical_cost: int
    abstained: bool
    fired_rules: int


def _assert_runtime_only(case: object) -> RuntimeRepairCase:
    for field in FORBIDDEN_RUNTIME_FIELDS:
        if hasattr(case, field):
            raise RuntimeSeparationError(f"case carries forbidden runtime field {field!r}")
    if not isinstance(case, RuntimeRepairCase):
        raise RuntimeSeparationError(f"execution requires RuntimeRepairCase, got {type(case).__name__}")
    return case


def _leaf_matches(predicate: Predicate, graph: FrozenRelationGraph) -> set[str]:
    edges = tuple(
        edge
        for edge in graph.edges
        if predicate.relation_edge_id is None
        or edge.edge_id == predicate.relation_edge_id
    )
    if predicate.kind is PredicateKind.DIVERGENT_PAIR_MEMBER:
        return {
            item_id
            for edge in edges
            if edge.relation == "same_slot_different_value"
            for item_id in (edge.left_item_id, edge.right_item_id)
        }
    if predicate.kind is PredicateKind.SUPERSEDED_ITEM:
        return {
            edge.actionability.target_item_id
            for edge in edges
            if edge.relation == "same_slot_different_value"
            and edge.actionability.mode is ActionMode.DESTRUCTIVE
            and edge.actionability.target_item_id is not None
            and (
                predicate.target_item_id is None
                or edge.actionability.target_item_id == predicate.target_item_id
            )
        }
    raise ValueError(f"unsupported v3 predicate leaf {predicate.kind.value!r}")


def _matches(predicate: Predicate, graph: FrozenRelationGraph, all_ids: set[str]) -> set[str]:
    if predicate.kind is PredicateKind.AND:
        results = [_matches(operand, graph, all_ids) for operand in predicate.operands]
        return set.intersection(*results)
    if predicate.kind is PredicateKind.OR:
        return set().union(*(_matches(operand, graph, all_ids) for operand in predicate.operands))
    if predicate.kind is PredicateKind.NOT:
        return all_ids - _matches(predicate.operands[0], graph, all_ids)
    return _leaf_matches(predicate, graph) & all_ids


_DISPOSITIONS = {ActionKind.DEMOTE: "demoted", ActionKind.SUPPRESS: "suppressed", ActionKind.REPLACE: "historical", ActionKind.ANNOTATE_CONFLICT: "conflict"}


def _apply(action: Action, state: RepairState, matched: set[str], *, node_id: str, predicate_id: str) -> tuple[RepairState, int, bool]:
    if action.kind in (ActionKind.KEEP, ActionKind.PRESERVE):
        return state, 0, False
    if action.kind in (ActionKind.ABSTAIN, ActionKind.VERIFY):
        return state, 0, True
    disposition = _DISPOSITIONS.get(action.kind)
    if disposition is None:
        raise ValueError(f"unimplemented v3 action {action.kind.value!r}")
    ordered = tuple(sorted(matched))
    return apply_disposition(state, item_ids=ordered, disposition=disposition, operator_node_id=node_id, predicate_id=predicate_id), len(ordered), False


def execute_program(
    program: Program, case: RuntimeRepairCase, state: RepairState, *, graph: FrozenRelationGraph,
    expected_graph_sha256: str, expected_protocol_manifest_sha256: str,
    bounds: ResourceBounds = REGISTERED_BOUNDS,
) -> ExecutionResult:
    """Execute a program only after exact graph/run/state binding succeeds.

    There is no model client, no gold input, and no partial-graph fallback in
    this module.  Missing or mismatched artifacts are hard failures.
    """
    runtime_case = _assert_runtime_only(case)
    if state.case_id != runtime_case.case_id:
        raise ValueError("state case_id mismatch")
    all_ids = tuple(item.item_id for item in state.items)
    graph.assert_matches(
        case=runtime_case,
        item_ids=all_ids,
        expected_graph_sha256=expected_graph_sha256,
        expected_protocol_manifest_sha256=expected_protocol_manifest_sha256,
    )
    canonical = canonicalize(program)
    check_resource_bounds(canonical, bounds=bounds)
    current, matched_total, logical_cost, fired, abstained = state, 0, 0, 0, False
    for index, rule in enumerate(iter_rules(canonical)):
        matched = _matches(rule.predicate, graph, set(all_ids))
        if not matched:
            continue
        matched_total += len(matched)
        current, cost, did_abstain = _apply(rule.action, current, matched, node_id=f"node{index}", predicate_id=rule.predicate.kind.value)
        logical_cost += cost
        abstained = abstained or did_abstain
        if cost:
            fired += 1
        if logical_cost > bounds.max_logical_cost:
            raise ExecutionLimitError(f"logical cost {logical_cost} exceeds {bounds.max_logical_cost}")
    return ExecutionResult(current, matched_total, logical_cost, abstained, fired)
