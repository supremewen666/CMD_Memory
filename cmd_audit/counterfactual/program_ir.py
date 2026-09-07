"""Route A E2: typed operator IR (BUILD SPEC §8.1-§8.3).

An open search needs an operator space that is unbounded in principle but
executable safely. This module is that space: a typed AST of registered
predicates and actions, parsed from plain data, canonicalized, and bounded.

Three properties are deliberate.

Nothing is executed. `parse_program` accepts only registered node kinds with a
closed key set, so a proposer that emits `__import__('os')`, a lambda, or any
free-form string reaches a parse error instead of an interpreter. There is no
`eval`, no `compile`, and no import of proposer-supplied names anywhere.

The AST cannot carry a case literal (§8.2). Rather than denylisting field names
one at a time -- which would leak the moment a proposer invents a new key --
every node validates against an exact allowed key set and refuses anything else.
A memory ID, a required phrase, or a replacement string therefore has nowhere to
be written.

The space is finite at any fixed depth. `SIMILARITY_ABOVE` and `AGE_GAP_ABOVE`
take a threshold from a frozen three-value grid rather than an arbitrary float,
which is what makes E0b's exhaustive depth <= 2 enumeration possible at all.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum

__all__ = [
    "PredicateKind",
    "ActionKind",
    "ParameterizedPredicateKinds",
    "CONNECTIVE_KINDS",
    "COMMUTATIVE_KINDS",
    "LEAF_PREDICATE_KINDS",
    "IDENTITY_ACTION_KINDS",
    "SIMILARITY_THRESHOLDS",
    "AGE_GAP_THRESHOLDS",
    "IR_GRAMMAR_VERSION",
    "ProgramParseError",
    "ProgramBoundsError",
    "IdentityActionError",
    "Predicate",
    "Action",
    "If",
    "Sequence",
    "Program",
    "ResourceBounds",
    "REGISTERED_BOUNDS",
    "parse_program",
    "program_to_mapping",
    "canonicalize",
    "canonical_ast_hash",
    "program_depth",
    "program_node_count",
    "program_action_count",
    "check_resource_bounds",
    "iter_rules",
]

IR_GRAMMAR_VERSION = "route-a-ir-v1"


class PredicateKind(str, Enum):
    """§8.1 predicate vocabulary: three connectives and nine leaf tests."""

    AND = "and"
    OR = "or"
    NOT = "not"
    QUERY_RELEVANT = "query_relevant"
    TEMPORAL_DOMINATES = "temporal_dominates"
    CONTRADICTS = "contradicts"
    SOURCE_MORE_RELIABLE = "source_more_reliable"
    PROVENANCE_MATCHES = "provenance_matches"
    SIMILARITY_ABOVE = "similarity_above"
    AGE_GAP_ABOVE = "age_gap_above"
    EVIDENCE_MISSING = "evidence_missing"


class ActionKind(str, Enum):
    """§8.1 action vocabulary."""

    KEEP = "keep"
    DEMOTE = "demote"
    SUPPRESS = "suppress"
    REPLACE = "replace"
    ANNOTATE_CONFLICT = "annotate_conflict"
    RETRIEVE_FILL = "retrieve_fill"
    PRESERVE = "preserve"
    ABSTAIN = "abstain"
    VERIFY = "verify"


CONNECTIVE_KINDS = (PredicateKind.AND, PredicateKind.OR, PredicateKind.NOT)
COMMUTATIVE_KINDS = (PredicateKind.AND, PredicateKind.OR)
LEAF_PREDICATE_KINDS = tuple(
    kind for kind in PredicateKind if kind not in CONNECTIVE_KINDS
)

#: Only these two leaves are parameterized. Every other predicate is a pure
#: structural test with no tunable number, which keeps the grammar finite.
ParameterizedPredicateKinds = (
    PredicateKind.SIMILARITY_ABOVE,
    PredicateKind.AGE_GAP_ABOVE,
)

#: Frozen threshold grids. A continuous parameter would make the depth <= 2
#: envelope infinite and E0b's exactness claim false.
SIMILARITY_THRESHOLDS = (0.25, 0.5, 0.75)
AGE_GAP_THRESHOLDS = (1.0, 7.0, 30.0)

_THRESHOLD_GRIDS = {
    PredicateKind.SIMILARITY_ABOVE: SIMILARITY_THRESHOLDS,
    PredicateKind.AGE_GAP_ABOVE: AGE_GAP_THRESHOLDS,
}

#: Actions that leave state byte-identical. A rule carrying one is not variation
#: (§8.3 "identity actions" removal), so canonicalization drops it.
IDENTITY_ACTION_KINDS = (ActionKind.KEEP, ActionKind.PRESERVE)

_PREDICATE_KEYS = frozenset({"kind", "operands", "threshold"})
_ACTION_KEYS = frozenset({"kind"})
_IF_KEYS = frozenset({"node", "predicate", "action"})
_SEQUENCE_KEYS = frozenset({"node", "body"})


class ProgramParseError(ValueError):
    """Raised on an unregistered node, an unexpected key, or a bad threshold."""


class ProgramBoundsError(ValueError):
    """Raised when a program exceeds a registered resource bound (§8.1)."""


class IdentityActionError(ValueError):
    """Raised when canonicalization leaves a program with no acting rule.

    A program whose every rule is `keep`/`preserve` cannot change state, so it
    is not a repair candidate. Failing here rather than returning an empty
    program keeps a no-op out of the proposal ledger, where it would occupy
    budget and appear in the envelope as a distinct candidate.
    """


@dataclass(frozen=True)
class Predicate:
    kind: PredicateKind
    operands: tuple["Predicate", ...] = ()
    threshold: float | None = None

    def __post_init__(self) -> None:
        if self.kind in COMMUTATIVE_KINDS:
            if len(self.operands) < 2:
                raise ProgramParseError(
                    f"{self.kind.value} needs at least two operands"
                )
        elif self.kind is PredicateKind.NOT:
            if len(self.operands) != 1:
                raise ProgramParseError("not takes exactly one operand")
        elif self.operands:
            raise ProgramParseError(f"{self.kind.value} takes no operands")
        if self.kind in ParameterizedPredicateKinds:
            grid = _THRESHOLD_GRIDS[self.kind]
            if self.threshold is None:
                raise ProgramParseError(f"{self.kind.value} requires a threshold")
            if self.threshold not in grid:
                raise ProgramParseError(
                    f"{self.kind.value} threshold {self.threshold} is off the "
                    f"registered grid {grid}"
                )
        elif self.threshold is not None:
            raise ProgramParseError(f"{self.kind.value} takes no threshold")


@dataclass(frozen=True)
class Action:
    kind: ActionKind

    @property
    def is_identity(self) -> bool:
        return self.kind in IDENTITY_ACTION_KINDS


@dataclass(frozen=True)
class If:
    predicate: Predicate
    action: Action


@dataclass(frozen=True)
class Sequence:
    body: tuple["Program", ...] = field(default=())


Program = If | Sequence


def _require_mapping(value: object, *, what: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ProgramParseError(f"{what} must be a mapping, got {type(value).__name__}")
    return value


def _require_keys(
    value: dict[str, object], allowed: frozenset[str], *, what: str
) -> None:
    extra = sorted(set(value) - allowed)
    if extra:
        raise ProgramParseError(f"{what} carries unregistered keys: {extra}")


def _parse_predicate(value: object) -> Predicate:
    mapping = _require_mapping(value, what="predicate")
    _require_keys(mapping, _PREDICATE_KEYS, what="predicate")
    raw_kind = mapping.get("kind")
    try:
        kind = PredicateKind(raw_kind)
    except ValueError as error:
        raise ProgramParseError(f"unregistered predicate kind: {raw_kind!r}") from error
    operands = tuple(
        _parse_predicate(operand) for operand in mapping.get("operands", ()) or ()
    )
    raw_threshold = mapping.get("threshold")
    threshold = None if raw_threshold is None else float(raw_threshold)
    return Predicate(kind=kind, operands=operands, threshold=threshold)


def _parse_action(value: object) -> Action:
    mapping = _require_mapping(value, what="action")
    _require_keys(mapping, _ACTION_KEYS, what="action")
    raw_kind = mapping.get("kind")
    try:
        return Action(kind=ActionKind(raw_kind))
    except ValueError as error:
        raise ProgramParseError(f"unregistered action kind: {raw_kind!r}") from error


def parse_program(value: object) -> Program:
    """Build a program from serialized data. Never evaluates its input."""
    mapping = _require_mapping(value, what="program")
    node = mapping.get("node")
    if node == "if":
        _require_keys(mapping, _IF_KEYS, what="if")
        return If(
            predicate=_parse_predicate(mapping.get("predicate")),
            action=_parse_action(mapping.get("action")),
        )
    if node == "sequence":
        _require_keys(mapping, _SEQUENCE_KEYS, what="sequence")
        return Sequence(
            body=tuple(parse_program(item) for item in mapping.get("body", ()) or ())
        )
    raise ProgramParseError(f"unregistered node kind: {node!r}")


def _predicate_to_mapping(predicate: Predicate) -> dict[str, object]:
    result: dict[str, object] = {"kind": predicate.kind.value}
    if predicate.operands:
        result["operands"] = [
            _predicate_to_mapping(operand) for operand in predicate.operands
        ]
    if predicate.threshold is not None:
        result["threshold"] = predicate.threshold
    return result


def program_to_mapping(program: Program) -> dict[str, object]:
    """Serialize for the proposal ledger and the spec JSONL surfaces."""
    if isinstance(program, If):
        return {
            "node": "if",
            "predicate": _predicate_to_mapping(program.predicate),
            "action": {"kind": program.action.kind.value},
        }
    return {
        "node": "sequence",
        "body": [program_to_mapping(item) for item in program.body],
    }


def _predicate_sort_key(predicate: Predicate) -> str:
    return json.dumps(_predicate_to_mapping(predicate), sort_keys=True)


def _canonical_predicate(predicate: Predicate) -> Predicate:
    """Normalize commutative order and collapse double negation (§8.3)."""
    if predicate.kind is PredicateKind.NOT:
        inner = _canonical_predicate(predicate.operands[0])
        if inner.kind is PredicateKind.NOT:
            return inner.operands[0]
        return Predicate(kind=PredicateKind.NOT, operands=(inner,))
    if predicate.kind in COMMUTATIVE_KINDS:
        operands: list[Predicate] = []
        for operand in predicate.operands:
            canonical = _canonical_predicate(operand)
            # Flatten same-connective nesting so And(a, And(b, c)) and
            # And(a, b, c) share a hash.
            if canonical.kind is predicate.kind:
                operands.extend(canonical.operands)
            else:
                operands.append(canonical)
        deduplicated: list[Predicate] = []
        for operand in sorted(operands, key=_predicate_sort_key):
            if operand not in deduplicated:
                deduplicated.append(operand)
        if len(deduplicated) == 1:
            return deduplicated[0]
        return Predicate(kind=predicate.kind, operands=tuple(deduplicated))
    return predicate


def _is_statically_unsatisfiable(predicate: Predicate) -> bool:
    """Detect the contradictions §8.3 asks to remove statically.

    Only the syntactic case is claimed: an `And` containing both a term and its
    negation. Deciding satisfiability in general is not attempted, so a rule
    that is unreachable for a semantic reason survives canonicalization and is
    caught later by its behavior fingerprint.
    """
    if predicate.kind is PredicateKind.NOT:
        return _is_statically_unsatisfiable(predicate.operands[0])
    if predicate.kind is PredicateKind.OR:
        return all(
            _is_statically_unsatisfiable(operand) for operand in predicate.operands
        )
    if predicate.kind is not PredicateKind.AND:
        return False
    if any(_is_statically_unsatisfiable(operand) for operand in predicate.operands):
        return True
    negated = {
        _predicate_sort_key(operand.operands[0])
        for operand in predicate.operands
        if operand.kind is PredicateKind.NOT
    }
    return any(
        _predicate_sort_key(operand) in negated for operand in predicate.operands
    )


def _canonical_rules(program: Program) -> list[If]:
    """Flatten to the ordered list of rules that can actually fire."""
    if isinstance(program, If):
        if program.action.is_identity:
            return []
        predicate = _canonical_predicate(program.predicate)
        if _is_statically_unsatisfiable(predicate):
            return []
        return [If(predicate=predicate, action=program.action)]
    rules: list[If] = []
    for item in program.body:
        rules.extend(_canonical_rules(item))
    # Adjacent duplicates only: a repeat separated by another rule can observe a
    # state the first pass did not, so collapsing it would change behavior.
    collapsed: list[If] = []
    for rule in rules:
        if collapsed and collapsed[-1] == rule:
            continue
        collapsed.append(rule)
    return collapsed


def canonicalize(program: Program) -> Program:
    """Apply the §8.3 removal list.

    An empty `Sequence` is the registered null program and canonicalizes to
    itself; a non-empty program that reduces to nothing raises, because it
    reached that state by carrying only no-ops.
    """
    if isinstance(program, Sequence) and not program.body:
        return Sequence(())
    rules = _canonical_rules(program)
    if not rules:
        raise IdentityActionError(
            "program has no acting rule after canonicalization"
        )
    if len(rules) == 1:
        return rules[0]
    return Sequence(tuple(rules))


def canonical_ast_hash(program: Program) -> str:
    """SHA-256 over the canonical serialized form (§8.3 `canonical_ast_hash`)."""
    payload = json.dumps(
        {
            "grammar": IR_GRAMMAR_VERSION,
            "program": program_to_mapping(canonicalize(program)),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _predicate_depth(predicate: Predicate) -> int:
    if not predicate.operands:
        return 0
    return 1 + max(_predicate_depth(operand) for operand in predicate.operands)


def program_depth(program: Program) -> int:
    """AST depth. A leaf-predicate rule is depth 1; each nesting adds one."""
    if isinstance(program, If):
        return 1 + _predicate_depth(program.predicate)
    if not program.body:
        return 0
    return 1 + max(program_depth(item) for item in program.body)


def _predicate_nodes(predicate: Predicate) -> int:
    return 1 + sum(_predicate_nodes(operand) for operand in predicate.operands)


def program_node_count(program: Program) -> int:
    """Total AST nodes, counting predicates, actions, rules, and sequences."""
    if isinstance(program, If):
        return 1 + _predicate_nodes(program.predicate) + 1
    return 1 + sum(program_node_count(item) for item in program.body)


def iter_rules(program: Program) -> tuple[If, ...]:
    """Ordered rules, flattened. Reflects execution order."""
    if isinstance(program, If):
        return (program,)
    rules: list[If] = []
    for item in program.body:
        rules.extend(iter_rules(item))
    return tuple(rules)


def program_action_count(program: Program) -> int:
    """Rules that can act. The static bound on actions applied per case."""
    return sum(1 for rule in iter_rules(program) if not rule.action.is_identity)


@dataclass(frozen=True)
class ResourceBounds:
    """§8.1. Frozen before E3; a program outside them fails closed."""

    max_depth: int
    max_nodes: int
    max_actions_per_case: int
    max_retrieved_additions: int
    max_token_delta: int
    max_logical_cost: int

    def as_mapping(self) -> dict[str, int]:
        return {
            "max_depth": self.max_depth,
            "max_nodes": self.max_nodes,
            "max_actions_per_case": self.max_actions_per_case,
            "max_retrieved_additions": self.max_retrieved_additions,
            "max_token_delta": self.max_token_delta,
            "max_logical_cost": self.max_logical_cost,
        }


#: The registered envelope. `max_depth=3` admits a sequence over one level of
#: predicate logic, which is one level above the depth <= 2 space E0b enumerates
#: exhaustively -- so synthesized novelty has somewhere to live.
REGISTERED_BOUNDS = ResourceBounds(
    max_depth=3,
    max_nodes=32,
    max_actions_per_case=4,
    max_retrieved_additions=4,
    max_token_delta=512,
    max_logical_cost=16,
)


def check_resource_bounds(
    program: Program, *, bounds: ResourceBounds = REGISTERED_BOUNDS
) -> None:
    """Raise `ProgramBoundsError` unless the static bounds hold.

    Only the statically decidable three are checked here. `max_retrieved_
    additions`, `max_token_delta`, and `max_logical_cost` depend on the case and
    are enforced during execution by the state executor.
    """
    depth = program_depth(program)
    if depth > bounds.max_depth:
        raise ProgramBoundsError(f"depth {depth} exceeds {bounds.max_depth}")
    nodes = program_node_count(program)
    if nodes > bounds.max_nodes:
        raise ProgramBoundsError(f"node count {nodes} exceeds {bounds.max_nodes}")
    actions = program_action_count(program)
    if actions > bounds.max_actions_per_case:
        raise ProgramBoundsError(
            f"action count {actions} exceeds {bounds.max_actions_per_case}"
        )
