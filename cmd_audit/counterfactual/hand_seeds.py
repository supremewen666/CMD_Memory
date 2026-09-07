"""The frozen hand-written seed population (§9.1), as typed IR.

Two stages need this set. §6.3 compares the cross-fitted closed-grammar winner
against `best_hand_seed`, and §9.1 puts "every behaviorally unique hand-written
seed approved by the E0 provenance audit" into E3's initial population. Both
need the same definition, so it lives here rather than inline in either command.

These are *not* the sanity arms in `experiments/build_dev_state_intents.py`. That
`hand_seed` arm reads `HiddenStateIntent` -- it suppresses exactly the recorded
perturbation targets and restores exactly the allowed additions -- which makes it
an oracle upper bound on what any operator could do, useful for proving the
fitness discriminates and useless as a baseline. §6.3's headroom must compare two
gold-free programs on one endpoint, or the difference measures oracle access
rather than grammar reach. Every seed here reads only the runtime surface.

Each seed is a repair heuristic a person would plausibly write after reading the
taxonomy, and no seed is tuned against dev outcomes: they were fixed from the
predicate/action vocabulary, and whichever wins, wins. `abstain-preserve` is
listed by §9.1 as a population member in its own right and is the registered null
program, so it appears here too and is what "the operator that does nothing"
means across E0, E0b, and E5.
"""

from __future__ import annotations

from dataclasses import dataclass

from cmd_audit.counterfactual.program_ir import (
    REGISTERED_BOUNDS,
    Action,
    ActionKind,
    If,
    Predicate,
    PredicateKind,
    Program,
    Sequence,
    canonical_ast_hash,
    canonicalize,
    check_resource_bounds,
    program_to_mapping,
)

__all__ = [
    "HAND_SEED_POPULATION_VERSION",
    "HandSeed",
    "HAND_SEEDS",
    "audit_seed_population",
    "hand_seed_manifest",
]

HAND_SEED_POPULATION_VERSION = "route-a-hand-seeds-v1"


@dataclass(frozen=True)
class HandSeed:
    """One named hand-written seed."""

    name: str
    program: Program
    rationale: str

    def canonical_ast_hash(self) -> str:
        return canonical_ast_hash(self.program)

    def as_mapping(self) -> dict[str, object]:
        return {
            "name": self.name,
            "canonical_ast_hash": self.canonical_ast_hash(),
            "rationale": self.rationale,
            "program": program_to_mapping(canonicalize(self.program)),
        }


def _leaf(kind: PredicateKind, threshold: float | None = None) -> Predicate:
    return Predicate(kind=kind, threshold=threshold)


_FILL = If(
    predicate=_leaf(PredicateKind.EVIDENCE_MISSING),
    action=Action(ActionKind.RETRIEVE_FILL),
)
_SUPPRESS_CONTRADICTION = If(
    predicate=_leaf(PredicateKind.CONTRADICTS),
    action=Action(ActionKind.SUPPRESS),
)
_DEMOTE_SUPERSEDED = If(
    predicate=_leaf(PredicateKind.TEMPORAL_DOMINATES),
    action=Action(ActionKind.DEMOTE),
)

#: Frozen before E0's provenance audit. Order is the tie-break order used when
#: two seeds score identically, so it is part of the freeze.
HAND_SEEDS: tuple[HandSeed, ...] = (
    HandSeed(
        name="abstain-preserve",
        program=Sequence(()),
        rationale=(
            "§9.1's registered null program. Touches nothing, so it is the "
            "reference for 'the repair did not help' and the arm that shows a "
            "positive endpoint is not an artifact of acting at all."
        ),
    ),
    HandSeed(
        name="fill-missing-evidence",
        program=_FILL,
        rationale=(
            "The most obvious heuristic: when recall does not cover the query, "
            "pull candidates from the pool. Coincides with the legacy "
            "retrieval_error translation, which is a finding about how little "
            "the closed grammar adds rather than a reason to perturb the seed."
        ),
    ),
    HandSeed(
        name="suppress-contradictions",
        program=_SUPPRESS_CONTRADICTION,
        rationale=(
            "Drop both sides of a same-slot contradiction rather than guess "
            "which is current. Costs recall to buy consistency, so it is the "
            "seed that tests whether the endpoint rewards caution."
        ),
    ),
    HandSeed(
        name="demote-superseded",
        program=_DEMOTE_SUPERSEDED,
        rationale=(
            "Keep both sides of a same-slot pair but push the later-ranked one "
            "down. The conservative counterpart to suppress-contradictions: it "
            "never withholds text, so it cannot lose a protected item."
        ),
    ),
    HandSeed(
        name="deconflict-unreliable-source",
        program=If(
            predicate=Predicate(
                kind=PredicateKind.AND,
                operands=(
                    _leaf(PredicateKind.CONTRADICTS),
                    _leaf(PredicateKind.SOURCE_MORE_RELIABLE),
                ),
            ),
            action=Action(ActionKind.DEMOTE),
        ),
        rationale=(
            "Break a contradiction by provenance instead of by recency: demote "
            "only the contradicting item that a more reliable sibling outranks. "
            "The one seed that uses store reliability, so its score separates "
            "provenance-driven repair from ordering-driven repair."
        ),
    ),
    HandSeed(
        name="fill-then-deconflict",
        program=Sequence((_FILL, _SUPPRESS_CONTRADICTION)),
        rationale=(
            "Composition matters: filling first can introduce the contradiction "
            "that the second rule then resolves, which a single rule cannot "
            "reach. Tests whether sequencing buys anything on this endpoint."
        ),
    ),
    HandSeed(
        name="deconflict-then-fill",
        program=Sequence((_SUPPRESS_CONTRADICTION, _FILL)),
        rationale=(
            "The reverse order. Paired with fill-then-deconflict so an order "
            "effect is observable rather than assumed; if both score the same, "
            "the endpoint is order-insensitive here and that is worth knowing."
        ),
    ),
    HandSeed(
        name="verify-relevant-then-fill",
        program=Sequence(
            (
                If(
                    predicate=_leaf(PredicateKind.QUERY_RELEVANT),
                    action=Action(ActionKind.VERIFY),
                ),
                _FILL,
            )
        ),
        rationale=(
            "Spends logical cost without changing state, then fills. Included "
            "so the cost tie-break in §10.1 has a candidate that is behaviorally "
            "close to fill-missing-evidence but strictly more expensive."
        ),
    ),
)


def audit_seed_population(seeds: tuple[HandSeed, ...] = HAND_SEEDS) -> None:
    """§6.1's provenance audit over a seed population.

    Every seed must canonicalize and sit inside the registered bounds, so a seed
    that cannot legally execute fails here rather than being silently skipped
    during E0 and quietly shrinking the baseline it is supposed to define.

    Takes the population as an argument so the audit itself is testable: a
    version that checked nothing would otherwise be indistinguishable from a
    version that checked everything, since the frozen population passes either
    way.
    """
    for seed in seeds:
        canonical = canonicalize(seed.program)
        check_resource_bounds(canonical, bounds=REGISTERED_BOUNDS)
    hashes = [seed.canonical_ast_hash() for seed in seeds]
    if len(set(hashes)) != len(hashes):
        raise ValueError("hand seed population contains an AST duplicate")


def hand_seed_manifest() -> dict[str, object]:
    """§9.1 population entry for the hand-written seeds."""
    audit_seed_population(HAND_SEEDS)
    return {
        "hand_seed_population_version": HAND_SEED_POPULATION_VERSION,
        "seed_count": len(HAND_SEEDS),
        "reads_hidden_intent": False,
        "seeds": [seed.as_mapping() for seed in HAND_SEEDS],
        "distinction_note": (
            "These are gold-free programs. The `hand_seed` arm in "
            "build_dev_state_intents.py reads HiddenStateIntent and is an oracle "
            "upper bound, not a baseline; using it in §6.3 would measure oracle "
            "access rather than grammar reach."
        ),
    }
