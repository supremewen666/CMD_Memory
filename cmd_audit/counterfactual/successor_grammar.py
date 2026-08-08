"""The successor predicate grammar: `route-a-ir-v1` plus one leaf, by delegation.

`slot_divergence` fires on 1200/1200 `stale_item` cases where `CONTRADICTS`
fires on 0. To be worth building it has to reach the operator search, and the
search reads `PredicateKind` from `program_ir`. But that vocabulary is frozen:
E0's artifacts carry `IR_GRAMMAR_VERSION = "route-a-ir-v1"`, and §11.3's
5482-class pre-search envelope was enumerated over exactly its nine leaves.
Appending a tenth member would leave every existing artifact naming a grammar
that no longer exists -- the same provenance break this project has already been
burned by once.

**Why delegation is sound and not a way around the freeze.** v1's
`_match_predicate` recurses through connectives only: `and`/`or`/`not` walk their
operands, and every leaf returns without recursing. A v1 leaf carries no
operands at all -- `Predicate.__post_init__` rejects them. So if this module
handles its own connectives and passes v1 only *leaves*, v1 code can never
receive a kind it does not know. v1 keeps evaluating exactly the programs it
always evaluated, and its version string keeps meaning what it meant. The
delegation is tested (`test_v1_never_receives_a_successor_kind`) because the
design depends on it rather than merely benefiting from it.

**Why the v1 leaves are delegated rather than reimplemented.** A second copy of
`EVIDENCE_MISSING` would be a second thing to keep in sync, and a divergence
between the two would be invisible: both would return item IDs and neither would
error. Delegating means there is exactly one definition of each v1 leaf, and it
is the frozen one.

**What this deliberately does not add.** No threshold. `SLOT_DIVERGES` takes no
parameter, and passing one raises. The sensor behind it has no free parameter
either -- the corroboration gate that would have been one was removed after
measuring that it never fired. This matters because `_SAME_SLOT_OVERLAP` is the
reason this module exists: an unexercised tunable inside a frozen evaluator is
what turned a dead relation into something that looked mistuned.

This module is a vocabulary and an evaluator. It does not register itself into
any search: a re-run of E0 under this grammar needs its own preregistration
recording the new envelope size, since the 5482 count is specific to v1.

Zero LLM calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .program_ir import Predicate, PredicateKind, ProgramParseError
from .repair_state import RepairStateItem
from .slot_divergence import SLOT_DIVERGENCE_VERSION, divergent_slot_pairs
from .state_executor import _match_predicate

__all__ = [
    "SUCCESSOR_GRAMMAR_VERSION",
    "SUCCESSOR_SENSOR_VERSIONS",
    "SuccessorPredicate",
    "SuccessorPredicateKind",
    "match_successor_predicate",
    "parse_successor_predicate",
    "successor_leaf_kinds",
]

#: Distinguishable from `route-a-ir-v1` in any artifact that used it. A later
#: reader must be able to tell which grammar produced a number, and the whole
#: point of a successor is that the predecessor's numbers stay valid.
SUCCESSOR_GRAMMAR_VERSION = "route-a-ir-v2-slot"

#: The sensors this grammar's new leaves stand on, by their own version strings.
#: Recorded here so an artifact naming this grammar pins the sensor behavior too
#: -- `route-a-ir-v2-slot` means nothing without knowing which
#: `slot_divergence` decided.
SUCCESSOR_SENSOR_VERSIONS = {
    "slot_diverges": SLOT_DIVERGENCE_VERSION,
}


class SuccessorPredicateKind(str, Enum):
    """v1's vocabulary, plus `SLOT_DIVERGES`.

    The v1 members carry v1's exact string values, so a v1 program's serialized
    form parses here unchanged. That is what makes the E0 winner comparable
    under this grammar rather than merely re-expressible in it.
    """

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
    #: The relation `CONTRADICTS` cannot express: same slot, different value.
    #: Measured 1200/1200 against its 0/1200 on `stale_item`.
    SLOT_DIVERGES = "slot_diverges"


_CONNECTIVE_KINDS = (
    SuccessorPredicateKind.AND,
    SuccessorPredicateKind.OR,
    SuccessorPredicateKind.NOT,
)

#: Leaves this module evaluates itself. Everything else is delegated to v1.
_SUCCESSOR_ONLY_KINDS = (SuccessorPredicateKind.SLOT_DIVERGES,)

#: v1's parameterized leaves. Repeated rather than imported as a set of strings
#: because the check below is about *this* enum's members.
_PARAMETERIZED_KINDS = (
    SuccessorPredicateKind.SIMILARITY_ABOVE,
    SuccessorPredicateKind.AGE_GAP_ABOVE,
)

_PREDICATE_KEYS = frozenset({"kind", "operands", "threshold"})


@dataclass(frozen=True)
class SuccessorPredicate:
    """One predicate node. Frozen for the same reason v1's is: it is recorded in
    a ledger, and a caller mutating one would change what a search reported
    after the fact."""

    kind: SuccessorPredicateKind
    operands: tuple["SuccessorPredicate", ...] = field(default=())
    threshold: float | None = None

    def __post_init__(self) -> None:
        if self.kind in (SuccessorPredicateKind.AND, SuccessorPredicateKind.OR):
            if len(self.operands) < 2:
                raise ProgramParseError(
                    f"{self.kind.value} needs at least two operands"
                )
        elif self.kind is SuccessorPredicateKind.NOT:
            if len(self.operands) != 1:
                raise ProgramParseError("not takes exactly one operand")
        elif self.operands:
            raise ProgramParseError(f"{self.kind.value} takes no operands")
        if self.kind not in _PARAMETERIZED_KINDS and self.threshold is not None:
            # The one place a free parameter could re-enter the sensor. The
            # relation either holds or it does not; there is nothing to tune.
            raise ProgramParseError(f"{self.kind.value} takes no threshold")
        if self.kind in _PARAMETERIZED_KINDS and self.threshold is None:
            raise ProgramParseError(f"{self.kind.value} requires a threshold")


def successor_leaf_kinds() -> tuple[SuccessorPredicateKind, ...]:
    """Leaf tests in this grammar. A superset of v1's, by one member."""
    return tuple(
        kind for kind in SuccessorPredicateKind if kind not in _CONNECTIVE_KINDS
    )


def parse_successor_predicate(value: object) -> SuccessorPredicate:
    """Build a predicate from serialized data. Never evaluates its input.

    Key validation is exact, as in v1: §8.2 says a case literal must have
    nowhere to be written, and an invented key is the place it would go.
    """
    if not isinstance(value, dict):
        raise ProgramParseError(
            f"predicate must be a mapping, got {type(value).__name__}"
        )
    extra = sorted(set(value) - _PREDICATE_KEYS)
    if extra:
        raise ProgramParseError(f"predicate carries unregistered keys: {extra}")
    raw_kind = value.get("kind")
    try:
        kind = SuccessorPredicateKind(raw_kind)
    except ValueError as error:
        raise ProgramParseError(
            f"unregistered predicate kind: {raw_kind!r}"
        ) from error
    operands = tuple(
        parse_successor_predicate(operand)
        for operand in value.get("operands", ()) or ()
    )
    raw_threshold = value.get("threshold")
    threshold = None if raw_threshold is None else float(raw_threshold)
    return SuccessorPredicate(kind=kind, operands=operands, threshold=threshold)


def _to_v1_leaf(predicate: SuccessorPredicate) -> Predicate:
    """The v1 node for a delegated leaf.

    Only ever called on a leaf: a v1 `Predicate` for a connective would need v1
    operands, and a successor-only operand cannot become one. Enforced by the
    caller rather than asserted here, and pinned by
    `test_v1_never_receives_a_successor_kind`.
    """
    return Predicate(
        kind=PredicateKind(predicate.kind.value),
        threshold=predicate.threshold,
    )


def match_successor_predicate(
    predicate: SuccessorPredicate,
    *,
    items: tuple[RepairStateItem, ...],
    case: object,
) -> set[str]:
    """Item IDs the predicate selects.

    Connectives are walked here so that v1 only ever sees leaves. `not` is taken
    over the items present, matching v1's `all_ids - matched`: the complement of
    a selection is a selection over the same state, not over the universe.
    """
    kind = predicate.kind

    if kind is SuccessorPredicateKind.AND:
        matched = match_successor_predicate(
            predicate.operands[0], items=items, case=case
        )
        for operand in predicate.operands[1:]:
            matched &= match_successor_predicate(operand, items=items, case=case)
        return matched
    if kind is SuccessorPredicateKind.OR:
        matched = set()
        for operand in predicate.operands:
            matched |= match_successor_predicate(operand, items=items, case=case)
        return matched
    if kind is SuccessorPredicateKind.NOT:
        present = {item.item_id for item in items}
        return present - match_successor_predicate(
            predicate.operands[0], items=items, case=case
        )

    if kind is SuccessorPredicateKind.SLOT_DIVERGES:
        return divergent_slot_pairs(items)

    # Every remaining kind is a v1 leaf, evaluated by the frozen module. There
    # is exactly one definition of each, and it is the one E0 ran.
    return _match_predicate(_to_v1_leaf(predicate), _StateView(items), case)


@dataclass(frozen=True)
class _StateView:
    """The `RepairState` surface v1's `_match_predicate` reads.

    It touches `state.items` and nothing else. Passing a real `RepairState`
    would mean synthesizing a rendered context and a state hash that no
    predicate looks at, and the extra fields would imply this function depends
    on more than it does.
    """

    items: tuple[RepairStateItem, ...]
