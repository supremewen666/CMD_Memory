"""Route A E0b: exact canonical enumeration of the shallow IR space (§6.4).

E0b freezes a pre-search comparison envelope by CPU enumeration rather than
synthesis, so that "Route A found something new" means "behavior outside this
frozen envelope" and not merely "syntax outside the legacy DSL". This module is
the enumerator.

Exactness is the whole point, so the two places where it is bounded are stated
here rather than left implicit in a loop bound.

`SHALLOW_MAX_DEPTH = 2` is the spec's depth. Under `program_depth`, a
`Sequence` of simple rules is itself depth 2, so the literal depth `<= 2` space
includes every ordered rule sequence up to the registered action bound of 4 --
48,030,108 four-rule sequences, roughly 23 hours of behavior fingerprinting and
tens of gigabytes of frozen JSONL.

`SHALLOW_SEQUENCE_LIMIT = 2` is a registered truncation adopted for that cost,
and it is a real reduction in coverage, not a free one. Measured on the frozen
probe suite: the 84 single-rule programs occupy 84 distinct behavior classes
(nothing collapses), and extending them to length 2 yields 3,761 further
classes from 6,972 candidates -- 54% of two-rule programs behave unlike anything
shorter. Sequence depth buys behavior here; the envelope simply stops paying for
it past two rules. `shallow_grammar_manifest` therefore publishes the enumerated
count, the full depth-2 size at the registered action bound, and the omitted
count, so a reader sees the boundary as a number rather than inferring
exhaustiveness the artifact does not have.

Every yielded program is already canonical. Enumerating a form that
canonicalization would rewrite -- a commutative pair in the other order, an
adjacent duplicate rule, `And(p, Not(p))` -- would count one behavior class
twice and inflate the envelope, which is the opposite of what it is for.

And/Or operands range over subsets of leaf instantiations, not over ordered
tuples: canonicalization sorts operands and drops duplicates, so a subset is
exactly one canonical predicate. Subsets that mix a leaf with its own negation
are not generated, since `_is_statically_unsatisfiable` deletes them.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterator

from cmd_audit.counterfactual.behavior_fingerprint import (
    PROBE_SUITE_VERSION,
    behavior_fingerprint,
    probe_suite_sha256,
)
from cmd_audit.counterfactual.program_ir import (
    AGE_GAP_THRESHOLDS,
    COMMUTATIVE_KINDS,
    IDENTITY_ACTION_KINDS,
    IR_GRAMMAR_VERSION,
    LEAF_PREDICATE_KINDS,
    REGISTERED_BOUNDS,
    SIMILARITY_THRESHOLDS,
    Action,
    ActionKind,
    If,
    ParameterizedPredicateKinds,
    Predicate,
    PredicateKind,
    Program,
    Sequence,
    canonical_ast_hash,
    program_to_mapping,
)

__all__ = [
    "SHALLOW_ENVELOPE_VERSION",
    "SHALLOW_MAX_DEPTH",
    "SHALLOW_SEQUENCE_LIMIT",
    "EnvelopeMember",
    "ShallowEnvelope",
    "acting_actions",
    "count_shallow_ir_space",
    "enumerate_shallow_programs",
    "leaf_instantiations",
    "shallow_envelope",
    "shallow_grammar_manifest",
    "shallow_predicates",
]

SHALLOW_ENVELOPE_VERSION = "route-a-shallow-ir-v1"

#: §6.4. The spec's depth bound for the exhaustive envelope.
SHALLOW_MAX_DEPTH = 2

#: Registered truncation: rules per enumerated sequence. See the module
#: docstring; `shallow_grammar_manifest` publishes the size of what this omits.
SHALLOW_SEQUENCE_LIMIT = 2

_THRESHOLD_GRIDS = {
    PredicateKind.SIMILARITY_ABOVE: SIMILARITY_THRESHOLDS,
    PredicateKind.AGE_GAP_ABOVE: AGE_GAP_THRESHOLDS,
}


@lru_cache(maxsize=1)
def leaf_instantiations() -> tuple[Predicate, ...]:
    """Every leaf predicate, with parameterized kinds expanded over their grid."""
    leaves: list[Predicate] = []
    for kind in LEAF_PREDICATE_KINDS:
        if kind in ParameterizedPredicateKinds:
            leaves.extend(
                Predicate(kind=kind, threshold=value)
                for value in _THRESHOLD_GRIDS[kind]
            )
        else:
            leaves.append(Predicate(kind=kind))
    return tuple(leaves)


@lru_cache(maxsize=1)
def acting_actions() -> tuple[Action, ...]:
    """Actions that can change state. Identity actions are not variation (§8.3)."""
    return tuple(
        Action(kind) for kind in ActionKind if kind not in IDENTITY_ACTION_KINDS
    )


def _canonical_operand_order(operands: tuple[Predicate, ...]) -> tuple[Predicate, ...]:
    """Sorted the way canonicalization sorts, so the built form is already canonical."""
    return tuple(
        sorted(operands, key=lambda p: json.dumps(_mapping(p), sort_keys=True))
    )


def _mapping(predicate: Predicate) -> dict[str, object]:
    result: dict[str, object] = {"kind": predicate.kind.value}
    if predicate.operands:
        result["operands"] = [_mapping(operand) for operand in predicate.operands]
    if predicate.threshold is not None:
        result["threshold"] = predicate.threshold
    return result


@lru_cache(maxsize=1)
def shallow_predicates() -> tuple[Predicate, ...]:
    """Every canonical predicate of at most one level of logic.

    Bare leaves, `Not(leaf)`, and `And`/`Or` over each leaf subset of size >= 2.
    Operands are subsets rather than tuples because canonicalization sorts and
    deduplicates them, making a subset exactly one canonical predicate.
    """
    leaves = leaf_instantiations()
    predicates: list[Predicate] = list(leaves)
    predicates.extend(
        Predicate(kind=PredicateKind.NOT, operands=(leaf,)) for leaf in leaves
    )
    for size in range(2, len(leaves) + 1):
        for combination in itertools.combinations(leaves, size):
            operands = _canonical_operand_order(combination)
            for connective in COMMUTATIVE_KINDS:
                predicates.append(Predicate(kind=connective, operands=operands))
    return tuple(predicates)


def count_shallow_ir_space(max_rules: int = SHALLOW_SEQUENCE_LIMIT) -> int:
    """Analytic size of the enumerated space. Must match the generator exactly."""
    predicates = len(shallow_predicates())
    actions = len(acting_actions())
    simple_rules = len(leaf_instantiations()) * actions
    total = 1 + predicates * actions  # null program + every single-rule program
    for length in range(2, max_rules + 1):
        # Sequences are built from simple (leaf-predicate) rules only: a
        # sequence over compound predicates is depth 3, outside this space.
        # No adjacent repeat, since canonicalization collapses one.
        total += simple_rules * (simple_rules - 1) ** (length - 1)
    return total


def enumerate_shallow_programs(
    max_rules: int = SHALLOW_SEQUENCE_LIMIT,
) -> Iterator[Program]:
    """Yield every canonical program in the space, exactly once, in fixed order."""
    yield Sequence(())
    actions = acting_actions()
    for predicate in shallow_predicates():
        for action in actions:
            yield If(predicate=predicate, action=action)

    simple_rules = tuple(
        If(predicate=leaf, action=action)
        for leaf in leaf_instantiations()
        for action in actions
    )
    for length in range(2, max_rules + 1):
        for combination in itertools.product(simple_rules, repeat=length):
            if any(
                combination[position] == combination[position + 1]
                for position in range(length - 1)
            ):
                continue
            yield Sequence(combination)


@dataclass(frozen=True)
class EnvelopeMember:
    program: Program
    canonical_ast_hash: str
    behavior_fingerprint: str
    collapsed_hashes: tuple[str, ...]

    def as_mapping(self) -> dict[str, object]:
        return {
            "canonical_ast_hash": self.canonical_ast_hash,
            "behavior_fingerprint": self.behavior_fingerprint,
            "collapsed_count": len(self.collapsed_hashes),
            "program": program_to_mapping(self.program),
        }


@dataclass(frozen=True)
class ShallowEnvelope:
    members: tuple[EnvelopeMember, ...]
    enumerated_count: int
    behavior_class_count: int
    collapsed_count: int
    sequence_limit: int

    def as_mapping(self) -> dict[str, object]:
        return {
            "shallow_envelope_version": SHALLOW_ENVELOPE_VERSION,
            "enumerated_count": self.enumerated_count,
            "behavior_class_count": self.behavior_class_count,
            "collapsed_count": self.collapsed_count,
            "sequence_limit": self.sequence_limit,
            "probe_suite_version": PROBE_SUITE_VERSION,
            "probe_suite_sha256": probe_suite_sha256(),
            "envelope_sha256": self.envelope_sha256(),
        }

    def envelope_sha256(self) -> str:
        """Digest over the frozen member set, for §6.4's pre-search freeze."""
        payload = json.dumps(
            {
                "shallow_envelope_version": SHALLOW_ENVELOPE_VERSION,
                "probe_suite_sha256": probe_suite_sha256(),
                "members": [
                    [member.canonical_ast_hash, member.behavior_fingerprint]
                    for member in self.members
                ],
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def shallow_envelope(
    max_rules: int = SHALLOW_SEQUENCE_LIMIT,
    programs: tuple[Program, ...] | None = None,
) -> ShallowEnvelope:
    """Enumerate, then collapse to one representative per behavior class.

    The first program of each class wins, so the envelope is order-stable and
    reproducible from the enumeration order alone.

    `programs` overrides the enumeration. E0b passes nothing and gets the full
    space; it exists because one fingerprint costs ~1.7 ms across the 64-probe
    suite, so collapsing the whole space is a minute-scale batch job rather than
    something a caller can do incidentally.
    """
    source = enumerate_shallow_programs(max_rules) if programs is None else programs
    by_fingerprint: dict[str, list[str]] = {}
    representatives: dict[str, tuple[Program, str]] = {}
    enumerated = 0
    for program in source:
        enumerated += 1
        ast_hash = canonical_ast_hash(program)
        fingerprint = behavior_fingerprint(program)
        if fingerprint in representatives:
            by_fingerprint[fingerprint].append(ast_hash)
            continue
        representatives[fingerprint] = (program, ast_hash)
        by_fingerprint[fingerprint] = []

    members = tuple(
        EnvelopeMember(
            program=program,
            canonical_ast_hash=ast_hash,
            behavior_fingerprint=fingerprint,
            collapsed_hashes=tuple(by_fingerprint[fingerprint]),
        )
        for fingerprint, (program, ast_hash) in representatives.items()
    )
    return ShallowEnvelope(
        members=members,
        enumerated_count=enumerated,
        behavior_class_count=len(members),
        collapsed_count=enumerated - len(members),
        sequence_limit=max_rules,
    )


def _depth2_space_size() -> int:
    """Size of the literal depth <= 2 space at the registered action bound.

    Published in the manifest so the registered truncation is visible as a
    number rather than as a claim.
    """
    return count_shallow_ir_space(REGISTERED_BOUNDS.max_actions_per_case)


def shallow_grammar_manifest(
    max_rules: int = SHALLOW_SEQUENCE_LIMIT,
) -> dict[str, object]:
    """§6.4 `shallow_ir_grammar_manifest.json`."""
    full = _depth2_space_size()
    enumerated = count_shallow_ir_space(max_rules)
    return {
        "shallow_envelope_version": SHALLOW_ENVELOPE_VERSION,
        "ir_grammar_version": IR_GRAMMAR_VERSION,
        "probe_suite_version": PROBE_SUITE_VERSION,
        "probe_suite_sha256": probe_suite_sha256(),
        "max_depth": SHALLOW_MAX_DEPTH,
        "sequence_limit": max_rules,
        "leaf_instantiation_count": len(leaf_instantiations()),
        "shallow_predicate_count": len(shallow_predicates()),
        "acting_action_count": len(acting_actions()),
        "enumerated_program_count": enumerated,
        "depth2_space_size_at_registered_action_bound": full,
        "omitted_program_count": full - enumerated,
        "truncation_reason": (
            "program_depth treats a Sequence of simple rules as depth 2, so the "
            "literal depth<=2 space includes every ordered rule sequence up to "
            f"max_actions_per_case={REGISTERED_BOUNDS.max_actions_per_case} "
            f"({full} programs), roughly 23 hours of behavior fingerprinting. "
            f"sequence_limit={max_rules} is registered before E3 as a cost "
            "truncation. It is a real coverage reduction: measured on the frozen "
            "probe suite, the 84 single-rule programs occupy 84 distinct behavior "
            "classes and extending them to length 2 yields 3761 further classes "
            "from 6972 candidates, so longer sequences do reach behavior shorter "
            "ones cannot. omitted_program_count states the size of what this "
            "envelope does not rule out."
        ),
        "truncation_is_lossy": True,
        "resource_bounds": REGISTERED_BOUNDS.as_mapping(),
    }
