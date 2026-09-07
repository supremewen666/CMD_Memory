"""Route A: execute a typed IR program against structured repair state (§3.2, §8.1).

The legacy repair surface applies an action to a rendered context string, so its
effect can only be recovered by reading prose back out. This executor is the
replacement: a program matches items by runtime predicate, and each firing rule
becomes one append-only trace event over `RepairState`.

Two boundaries are enforced here rather than trusted.

Gold separation (§14.1): `execute_program` rejects any case object carrying a
forbidden attribute before it reads a single field, so a `ProbeCase` -- or a
subclass that grew a `gold_answer` -- cannot reach a synthesized program even
if a caller passes one by mistake.

The three case-dependent resource bounds (§8.1) are checked while executing,
not after. `max_retrieved_additions`, `max_token_delta`, and `max_logical_cost`
depend on what the program actually matched, so a program that would exceed one
fails closed with `ExecutionLimitError` instead of returning an over-budget
state that a later gate would have to notice.

Predicates read only what a `RuntimeMemoryItem` carries: text, store, rank, and
source event IDs. `TEMPORAL_DOMINATES` therefore reads rank order, since the
runtime surface has no timestamp -- the same constraint that leaves the hook's
two recency factors hardcoded to zero.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from cmd_audit.counterfactual.program_ir import (
    Action,
    ActionKind,
    If,
    Predicate,
    PredicateKind,
    Program,
    REGISTERED_BOUNDS,
    ResourceBounds,
    Sequence,
    canonicalize,
    check_resource_bounds,
    iter_rules,
)
from cmd_audit.counterfactual.repair_state import (
    RepairState,
    RepairStateItem,
    add_item,
    apply_disposition,
    count_tokens,
)
from cmd_audit.eval.state_intent import (
    FORBIDDEN_RUNTIME_FIELDS,
    RuntimeRepairCase,
    RuntimeSeparationError,
)

__all__ = [
    "NULL_PROGRAM",
    "RELIABLE_STORES",
    "ExecutionLimitError",
    "ExecutionResult",
    "execute_program",
]

#: The registered do-nothing program. An empty `Sequence` is the one program
#: canonicalization admits with no acting rule (`abstain-preserve` in §9.1).
NULL_PROGRAM = Sequence(())

#: Stores whose provenance the pipeline treats as authoritative. Read from the
#: runtime `store` field, which is not gold: it records where the item came from.
RELIABLE_STORES = ("verified", "source", "document", "tool")

_NEGATION_WORDS = frozenset(
    {"not", "no", "never", "none", "neither", "nor", "without"}
)

# Same tokenizer the hook and structural router use, so a predicate here and a
# live confidence factor agree on what a token is.
_TOKEN_RE = re.compile(r"[A-Za-z0-9']+")

#: Query coverage at or below this fraction counts as missing evidence. Matches
#: the hook's evidence-presence branch: partial coverage is still a gap.
_EVIDENCE_COVERAGE_FLOOR = 0.5

#: Jaccard overlap above which two items are treated as making a claim about the
#: same slot, for `CONTRADICTS` and `TEMPORAL_DOMINATES`.
_SAME_SLOT_OVERLAP = 0.3


class ExecutionLimitError(ValueError):
    """A case-dependent resource bound was exceeded (§8.1 fail closed)."""


@dataclass(frozen=True)
class ExecutionResult:
    state: RepairState
    matched_item_count: int
    retrieved_additions: int
    token_delta: int
    logical_cost: int
    abstained: bool
    fired_rules: int

    def as_mapping(self) -> dict[str, object]:
        return {
            "state_hash": self.state.state_hash,
            "matched_item_count": self.matched_item_count,
            "retrieved_additions": self.retrieved_additions,
            "token_delta": self.token_delta,
            "logical_cost": self.logical_cost,
            "abstained": self.abstained,
            "fired_rules": self.fired_rules,
        }


def _tokens(text: str) -> set[str]:
    return {token.casefold() for token in _TOKEN_RE.findall(text)}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _assert_runtime_only(case: object) -> None:
    """§14.1. Refuse a case object that carries a forbidden field."""
    for forbidden in FORBIDDEN_RUNTIME_FIELDS:
        if hasattr(case, forbidden):
            raise RuntimeSeparationError(
                f"case carries forbidden runtime field {forbidden!r}"
            )
    if not isinstance(case, RuntimeRepairCase):
        raise RuntimeSeparationError(
            f"execution requires a RuntimeRepairCase, got {type(case).__name__}"
        )


def _contradiction_pairs(
    items: tuple[RepairStateItem, ...],
) -> set[str]:
    """Item IDs in a negation-polarity disagreement over shared content.

    The same structural test the live hook uses for its conflict factor: two
    items talking about the same thing where exactly one is negated.
    """
    matched: set[str] = set()
    token_sets = [(item.item_id, _tokens(item.text)) for item in items]
    for index, (left_id, left) in enumerate(token_sets):
        for right_id, right in token_sets[index + 1 :]:
            if bool(left & _NEGATION_WORDS) == bool(right & _NEGATION_WORDS):
                continue
            if _jaccard(left, right) > _SAME_SLOT_OVERLAP:
                matched.add(left_id)
                matched.add(right_id)
    return matched


def _temporally_dominated(items: tuple[RepairStateItem, ...]) -> set[str]:
    """Later-ranked members of a same-slot pair.

    A `RuntimeMemoryItem` carries no timestamp, so recall rank is the only
    ordering signal available. This predicate names the item that a
    recency-preferring policy would treat as superseding its partner; whether
    demoting or keeping it is the right repair is left to fitness.
    """
    dominated: set[str] = set()
    token_sets = [(item, _tokens(item.text)) for item in items]
    for index, (left, left_tokens) in enumerate(token_sets):
        for right, right_tokens in token_sets[index + 1 :]:
            if _jaccard(left_tokens, right_tokens) <= _SAME_SLOT_OVERLAP:
                continue
            later = left if left.rank > right.rank else right
            dominated.add(later.item_id)
    return dominated


def _query_uncovered(state: RepairState, case: RuntimeRepairCase) -> bool:
    query_tokens = _tokens(case.query)
    if not query_tokens:
        return False
    covered: set[str] = set()
    for item in state.items:
        covered |= query_tokens & _tokens(item.text)
    return len(covered) / len(query_tokens) <= _EVIDENCE_COVERAGE_FLOOR


def _similar_above(
    items: tuple[RepairStateItem, ...], threshold: float
) -> set[str]:
    matched: set[str] = set()
    token_sets = [(item.item_id, _tokens(item.text)) for item in items]
    for index, (left_id, left) in enumerate(token_sets):
        for right_id, right in token_sets[index + 1 :]:
            if _jaccard(left, right) >= threshold:
                matched.add(left_id)
                matched.add(right_id)
    return matched


def _match_predicate(
    predicate: Predicate,
    state: RepairState,
    case: RuntimeRepairCase,
) -> set[str]:
    """Item IDs the predicate selects. Reads runtime fields only."""
    kind = predicate.kind
    all_ids = {item.item_id for item in state.items}

    if kind is PredicateKind.AND:
        matched = _match_predicate(predicate.operands[0], state, case)
        for operand in predicate.operands[1:]:
            matched &= _match_predicate(operand, state, case)
        return matched
    if kind is PredicateKind.OR:
        matched: set[str] = set()
        for operand in predicate.operands:
            matched |= _match_predicate(operand, state, case)
        return matched
    if kind is PredicateKind.NOT:
        return all_ids - _match_predicate(predicate.operands[0], state, case)

    if kind is PredicateKind.QUERY_RELEVANT:
        query_tokens = _tokens(case.query)
        return {
            item.item_id
            for item in state.items
            if query_tokens & _tokens(item.text)
        }
    if kind is PredicateKind.CONTRADICTS:
        return _contradiction_pairs(state.items)
    if kind is PredicateKind.TEMPORAL_DOMINATES:
        return _temporally_dominated(state.items)
    if kind is PredicateKind.SOURCE_MORE_RELIABLE:
        # Fires on the items a more reliable sibling outranks, which is the set
        # a repair would act on. With no reliable item present nothing fires.
        if not any(item.store in RELIABLE_STORES for item in state.items):
            return set()
        return {
            item.item_id
            for item in state.items
            if item.store not in RELIABLE_STORES
        }
    if kind is PredicateKind.PROVENANCE_MATCHES:
        known_events = {event.event_id for event in case.raw_events}
        return {
            item.item_id
            for item in state.items
            if item.source_event_ids
            and set(item.source_event_ids) <= known_events
        }
    if kind is PredicateKind.SIMILARITY_ABOVE:
        assert predicate.threshold is not None  # guaranteed by Predicate
        return _similar_above(state.items, predicate.threshold)
    if kind is PredicateKind.AGE_GAP_ABOVE:
        # No timestamp on the runtime surface, so "age gap" is expressed as a
        # rank gap: items sitting at least `threshold` positions below the top.
        assert predicate.threshold is not None
        return {
            item.item_id
            for item in state.items
            if item.rank >= predicate.threshold
        }
    if kind is PredicateKind.EVIDENCE_MISSING:
        # A whole-case condition, not a per-item one: it selects every item so a
        # paired action (notably RETRIEVE_FILL) has a non-empty matched set.
        return all_ids if _query_uncovered(state, case) else set()
    raise ValueError(f"unhandled predicate kind: {kind}")


_DISPOSITION_FOR_ACTION = {
    ActionKind.DEMOTE: "demoted",
    ActionKind.SUPPRESS: "suppressed",
    ActionKind.ANNOTATE_CONFLICT: "conflict",
    # `Replace` cannot write text -- §8.2 forbids a literal in the AST -- so it
    # retires the matched item to `historical` and leaves the replacement to
    # whatever else is in state or gets filled in.
    ActionKind.REPLACE: "historical",
}


def _pool_items(
    case: RuntimeRepairCase, state: RepairState
) -> tuple[tuple[str, str, tuple[str, ...], str, int], ...]:
    """Unretrieved candidates not already in state, in rank order."""
    present = {item.item_id for item in state.items}
    return tuple(
        (item.item_id, item.text, tuple(item.source_event_ids), item.store, item.rank)
        for item in sorted(case.items, key=lambda i: (i.rank, i.item_id))
        if not item.retrieved and item.item_id not in present
    )


def execute_program(
    program: Program,
    case: RuntimeRepairCase,
    state: RepairState,
    *,
    bounds: ResourceBounds = REGISTERED_BOUNDS,
) -> ExecutionResult:
    """Run `program` against `state`. Deterministic, zero LLM calls."""
    _assert_runtime_only(case)

    if isinstance(program, Sequence) and not program.body:
        rules: tuple[If, ...] = ()
    else:
        canonical = canonicalize(program)
        check_resource_bounds(canonical, bounds=bounds)
        rules = iter_rules(canonical)

    starting_tokens = state.token_count
    current = state
    matched_total = 0
    additions = 0
    logical_cost = 0
    abstained = False
    fired = 0

    for index, rule in enumerate(rules):
        matched = _match_predicate(rule.predicate, current, case)
        if not matched:
            continue
        matched_total += len(matched)
        node_id = f"node{index}"
        predicate_id = rule.predicate.kind.value
        current, cost, added, did_abstain = _apply_action(
            rule.action,
            current,
            case,
            matched=matched,
            node_id=node_id,
            predicate_id=predicate_id,
            bounds=bounds,
            additions_so_far=additions,
        )
        additions += added
        logical_cost += cost
        abstained = abstained or did_abstain
        if cost or added:
            fired += 1
        if logical_cost > bounds.max_logical_cost:
            raise ExecutionLimitError(
                f"logical cost {logical_cost} exceeds {bounds.max_logical_cost}"
            )
        token_delta = current.token_count - starting_tokens
        if token_delta > bounds.max_token_delta:
            raise ExecutionLimitError(
                f"token delta {token_delta} exceeds {bounds.max_token_delta}"
            )

    return ExecutionResult(
        state=current,
        matched_item_count=matched_total,
        retrieved_additions=additions,
        token_delta=current.token_count - starting_tokens,
        logical_cost=logical_cost,
        abstained=abstained,
        fired_rules=fired,
    )


def _apply_action(
    action: Action,
    state: RepairState,
    case: RuntimeRepairCase,
    *,
    matched: set[str],
    node_id: str,
    predicate_id: str,
    bounds: ResourceBounds,
    additions_so_far: int,
) -> tuple[RepairState, int, int, bool]:
    """Apply one action. Returns `(state, logical_cost, additions, abstained)`."""
    kind = action.kind

    if action.is_identity:
        # Canonicalization removes these, so reaching here means a caller
        # executed a non-canonical program; leave state alone either way.
        return state, 0, 0, False

    if kind is ActionKind.ABSTAIN:
        return state, 0, 0, True

    if kind is ActionKind.VERIFY:
        # A check costs budget and changes nothing. It exists so a program can
        # spend cost without acting, which the cost gate must be able to see.
        return state, len(matched), 0, False

    if kind is ActionKind.RETRIEVE_FILL:
        pool = _pool_items(case, state)
        if not pool:
            return state, 0, 0, False
        query_tokens = _tokens(case.query)
        relevant = [
            entry for entry in pool if query_tokens & _tokens(entry[1])
        ] or list(pool)
        current = state
        added = 0
        for item_id, text, events, store, _rank in relevant:
            if additions_so_far + added + 1 > bounds.max_retrieved_additions:
                raise ExecutionLimitError(
                    f"retrieved additions exceed {bounds.max_retrieved_additions}"
                )
            projected = current.token_count + count_tokens(text)
            if projected - state.token_count > bounds.max_token_delta:
                raise ExecutionLimitError(
                    f"token delta exceeds {bounds.max_token_delta}"
                )
            current = add_item(
                current,
                item_id=item_id,
                text=text,
                source_event_ids=events,
                store=store,
                operator_node_id=node_id,
                predicate_id=predicate_id,
            )
            added += 1
        return current, added, added, False

    disposition = _DISPOSITION_FOR_ACTION.get(kind)
    if disposition is None:
        raise ValueError(f"unhandled action kind: {kind}")
    ordered = tuple(sorted(matched))
    return (
        apply_disposition(
            state,
            item_ids=ordered,
            disposition=disposition,
            operator_node_id=node_id,
            predicate_id=predicate_id,
        ),
        len(ordered),
        0,
        False,
    )
