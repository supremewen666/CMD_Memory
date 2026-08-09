"""V3 typed grammar for frozen semantic relation/actionability graphs.

The v3 vocabulary intentionally prevents the common unsafe composition:
``DIVERGENT_PAIR_MEMBER -> DEMOTE``.  Only a target selected by an independent
actionability verdict can enter a destructive action.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum

from cmd_audit.counterfactual.program_ir import ActionKind, ResourceBounds, REGISTERED_BOUNDS

IR_GRAMMAR_VERSION = "route-a-ir-v3-semantic-actionability"

__all__ = [
    "IR_GRAMMAR_VERSION", "PredicateKind", "ActionKind", "Action", "Predicate", "If", "Sequence", "Program",
    "ProgramParseError", "ProgramBoundsError", "ResourceBounds", "REGISTERED_BOUNDS", "parse_program", "program_to_mapping",
    "canonicalize", "canonical_ast_hash", "iter_rules", "check_resource_bounds",
]


class PredicateKind(str, Enum):
    AND = "and"
    OR = "or"
    NOT = "not"
    DIVERGENT_PAIR_MEMBER = "divergent_pair_member"
    SUPERSEDED_ITEM = "superseded_item"


CONNECTIVES = frozenset({PredicateKind.AND, PredicateKind.OR, PredicateKind.NOT})
DESTRUCTIVE = frozenset({ActionKind.DEMOTE, ActionKind.SUPPRESS, ActionKind.REPLACE})
_SAFE_DIVERGENT_ACTIONS = frozenset(
    {
        ActionKind.KEEP,
        ActionKind.PRESERVE,
        ActionKind.ANNOTATE_CONFLICT,
        ActionKind.ABSTAIN,
        ActionKind.VERIFY,
    }
)
_REGISTERED_ACTIONS = frozenset(
    {
        ActionKind.KEEP,
        ActionKind.PRESERVE,
        ActionKind.ANNOTATE_CONFLICT,
        ActionKind.ABSTAIN,
        ActionKind.VERIFY,
        ActionKind.DEMOTE,
        ActionKind.SUPPRESS,
        ActionKind.REPLACE,
    }
)


class ProgramParseError(ValueError):
    pass


class ProgramBoundsError(ValueError):
    pass


@dataclass(frozen=True)
class Predicate:
    kind: PredicateKind
    operands: tuple["Predicate", ...] = ()
    relation_edge_id: str | None = None
    target_item_id: str | None = None

    def __post_init__(self) -> None:
        if self.kind in (PredicateKind.AND, PredicateKind.OR) and len(self.operands) < 2:
            raise ProgramParseError(f"{self.kind.value} needs at least two operands")
        if self.kind is PredicateKind.NOT and len(self.operands) != 1:
            raise ProgramParseError("not takes exactly one operand")
        if self.kind not in CONNECTIVES and self.operands:
            raise ProgramParseError(f"{self.kind.value} takes no operands")
        if self.kind is PredicateKind.SUPERSEDED_ITEM:
            if (self.relation_edge_id is None) != (self.target_item_id is None):
                raise ProgramParseError(
                    "superseded_item exact binding needs edge and target together"
                )
        elif self.kind is PredicateKind.DIVERGENT_PAIR_MEMBER:
            if self.target_item_id is not None:
                raise ProgramParseError(
                    "divergent_pair_member cannot bind a destructive target"
                )
        elif self.relation_edge_id is not None or self.target_item_id is not None:
            raise ProgramParseError("only predicate leaves may carry graph bindings")
        if self.relation_edge_id is not None and not re.fullmatch(
            r"[0-9a-f]{64}", self.relation_edge_id
        ):
            raise ProgramParseError("relation_edge_id must be lowercase SHA-256")
        if self.target_item_id is not None and not self.target_item_id:
            raise ProgramParseError("target_item_id must be non-empty")


@dataclass(frozen=True)
class Action:
    kind: ActionKind

    def __post_init__(self) -> None:
        if self.kind not in _REGISTERED_ACTIONS:
            raise ProgramParseError(
                f"action {self.kind.value!r} is not implemented by the v3 executor"
            )

    @property
    def is_identity(self) -> bool:
        return self.kind in (ActionKind.KEEP, ActionKind.PRESERVE)


@dataclass(frozen=True)
class If:
    predicate: Predicate
    action: Action

    def __post_init__(self) -> None:
        if self.predicate.kind is PredicateKind.DIVERGENT_PAIR_MEMBER and self.action.kind not in _SAFE_DIVERGENT_ACTIONS:
            raise ProgramParseError("divergent_pair_member permits only annotate, abstain, or identity actions")
        # A target action is deliberately *not* refinable by a connective.  A
        # later extension needs a fresh grammar/envelope/audit freeze, rather
        # than smuggling a v1 query or evidence leaf into v3.
        if self.action.kind in DESTRUCTIVE and self.predicate.kind is not PredicateKind.SUPERSEDED_ITEM:
            raise ProgramParseError(
                "destructive actions require exactly superseded_item"
            )


@dataclass(frozen=True)
class Sequence:
    body: tuple["Program", ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if any(not isinstance(item, (If, Sequence)) for item in self.body):
            raise ProgramParseError("v3 sequence may contain only program nodes")
        if len(iter_rules(self)) > REGISTERED_BOUNDS.max_actions_per_case:
            raise ProgramBoundsError("v3 sequence exceeds max actions per case")


Program = If | Sequence


def _mapping(value: object, what: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ProgramParseError(f"{what} must be a mapping")
    return value


def _keys(value: dict[str, object], allowed: set[str], what: str) -> None:
    extra = set(value) - allowed
    if extra:
        raise ProgramParseError(f"{what} carries unregistered keys: {sorted(extra)}")


def _parse_predicate(
    value: object,
    *,
    depth: int = 1,
    node_count: list[int] | None = None,
) -> Predicate:
    if depth > REGISTERED_BOUNDS.max_depth:
        raise ProgramBoundsError("v3 predicate exceeds registered depth")
    if node_count is None:
        node_count = [0]
    node_count[0] += 1
    if node_count[0] > REGISTERED_BOUNDS.max_nodes:
        raise ProgramBoundsError("v3 predicate exceeds registered node count")
    mapping = _mapping(value, "predicate")
    _keys(
        mapping,
        {"kind", "operands", "relation_edge_id", "target_item_id"},
        "predicate",
    )
    try:
        kind = PredicateKind(mapping.get("kind"))
    except ValueError as error:
        raise ProgramParseError(f"unregistered predicate kind: {mapping.get('kind')!r}") from error
    return Predicate(
        kind=kind,
        operands=tuple(
            _parse_predicate(
                item,
                depth=depth + 1,
                node_count=node_count,
            )
            for item in mapping.get("operands", ()) or ()
        ),
        relation_edge_id=mapping.get("relation_edge_id"),
        target_item_id=mapping.get("target_item_id"),
    )


def _parse_action(value: object) -> Action:
    mapping = _mapping(value, "action")
    _keys(mapping, {"kind"}, "action")
    try:
        return Action(ActionKind(mapping.get("kind")))
    except ValueError as error:
        raise ProgramParseError(f"unregistered action kind: {mapping.get('kind')!r}") from error


def _parse_program(
    value: object,
    *,
    program_depth: int,
    node_count: list[int],
) -> Program:
    if program_depth > REGISTERED_BOUNDS.max_nodes:
        raise ProgramBoundsError("v3 program nesting exceeds registered node count")
    node_count[0] += 1
    if node_count[0] > REGISTERED_BOUNDS.max_nodes:
        raise ProgramBoundsError("v3 program exceeds registered node count")
    mapping = _mapping(value, "program")
    node = mapping.get("node")
    if node == "if":
        _keys(mapping, {"node", "predicate", "action"}, "if")
        program = If(
            _parse_predicate(mapping.get("predicate"), node_count=node_count),
            _parse_action(mapping.get("action")),
        )
        check_resource_bounds(program)
        return program
    if node == "sequence":
        _keys(mapping, {"node", "body"}, "sequence")
        program = Sequence(
            tuple(
                _parse_program(
                    item,
                    program_depth=program_depth + 1,
                    node_count=node_count,
                )
                for item in mapping.get("body", ()) or ()
            )
        )
        check_resource_bounds(program)
        return program
    raise ProgramParseError(f"unregistered node kind: {node!r}")


def parse_program(value: object) -> Program:
    return _parse_program(value, program_depth=1, node_count=[0])


def _predicate_mapping(predicate: Predicate) -> dict[str, object]:
    result: dict[str, object] = {"kind": predicate.kind.value}
    if predicate.operands:
        result["operands"] = [_predicate_mapping(item) for item in predicate.operands]
    if predicate.relation_edge_id is not None:
        result["relation_edge_id"] = predicate.relation_edge_id
    if predicate.target_item_id is not None:
        result["target_item_id"] = predicate.target_item_id
    return result


def program_to_mapping(program: Program) -> dict[str, object]:
    if isinstance(program, If):
        return {"node": "if", "predicate": _predicate_mapping(program.predicate), "action": {"kind": program.action.kind.value}}
    return {"node": "sequence", "body": [program_to_mapping(item) for item in program.body]}


def _canonical_predicate(predicate: Predicate) -> Predicate:
    if predicate.kind is PredicateKind.NOT:
        inner = _canonical_predicate(predicate.operands[0])
        return inner.operands[0] if inner.kind is PredicateKind.NOT else Predicate(PredicateKind.NOT, (inner,))
    if predicate.kind in (PredicateKind.AND, PredicateKind.OR):
        operands: list[Predicate] = []
        for item in predicate.operands:
            canonical = _canonical_predicate(item)
            operands.extend(canonical.operands if canonical.kind is predicate.kind else (canonical,))
        unique = {json.dumps(_predicate_mapping(item), sort_keys=True): item for item in operands}
        return Predicate(predicate.kind, tuple(unique[key] for key in sorted(unique)))
    return predicate


def iter_rules(program: Program) -> tuple[If, ...]:
    if isinstance(program, If):
        return (program,)
    return tuple(rule for item in program.body for rule in iter_rules(item))


def canonicalize(program: Program) -> Program:
    if isinstance(program, Sequence) and not program.body:
        return program
    rules = [If(_canonical_predicate(rule.predicate), rule.action) for rule in iter_rules(program) if not rule.action.is_identity]
    if not rules:
        return Sequence(())
    return rules[0] if len(rules) == 1 else Sequence(tuple(rules))


def canonical_ast_hash(program: Program) -> str:
    payload = json.dumps({"grammar": IR_GRAMMAR_VERSION, "program": program_to_mapping(canonicalize(program))}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _depth(predicate: Predicate) -> int:
    return 0 if not predicate.operands else 1 + max(_depth(item) for item in predicate.operands)


def check_resource_bounds(program: Program, *, bounds: ResourceBounds = REGISTERED_BOUNDS) -> None:
    rules = iter_rules(program)
    depth = max((1 + _depth(rule.predicate) for rule in rules), default=0)
    nodes = sum(1 for _ in _walk_program(program)) + sum(
        2 + sum(1 for _ in _walk(rule.predicate)) for rule in rules
    )
    if depth > bounds.max_depth or nodes > bounds.max_nodes or len(rules) > bounds.max_actions_per_case:
        raise ProgramBoundsError("v3 program exceeds registered resource bounds")


def _walk(predicate: Predicate):
    for child in predicate.operands:
        yield child
        yield from _walk(child)


def _walk_program(program: Program):
    yield program
    if isinstance(program, Sequence):
        for child in program.body:
            yield from _walk_program(child)
