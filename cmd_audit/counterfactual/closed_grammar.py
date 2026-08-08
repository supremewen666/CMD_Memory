"""Route A E0: the sealed legacy grammar, enumerated on the state surface (§6.1).

E0 answers a narrow question: how much of Route A's target headroom is already
reachable by the operators the live system shipped? If the closed legacy DSL
gets there on its own, open synthesis has nothing to add and §6.3 stops the
route. So this module has to enumerate the legacy grammar *exactly* and score it
on the *same* endpoint the synthesized artifact will later be scored on.

Two design points carry most of the weight.

**Sequences are stage lists, not one spec.** §6.1 fixes the generation point to
0 and allows sequence length 0..3, but `OperatorSpec.from_actions` rejects
duplicate generation points -- one spec cannot hold three actions at point 0.
The legal shape is an ordered tuple of single-action stages, which is also what
`CompositeOperatorSpec` documents ("two stages may intentionally act at the same
generation point, and flattening would erase the causal order"). A
`ClosedGrammarSpec` here is that stage list, and it deliberately does not
collapse to an `OperatorSpec`.

**The legacy actions are re-expressed as state transitions, not as prose.**
Legacy `apply_pipeline_action` appends rendered text blocks to a context string.
`state_success` is measured on `RepairState`, and §12.1 assigns
`state_executor.py` the job of "structured legacy and typed-IR state
transitions". Scoring a string-append against a state endpoint would compare two
different things, so each action's frozen SELECT x TRANSFORM mapping from
`PIPELINE_ACTION_OPERATOR_DSL` is translated into the typed IR that expresses
the same selector and transform on state:

    RETRIEVAL_ERROR   MISSED_CANDIDATES x ADD_FROM_STORE
        -> If(evidence_missing, retrieve_fill)
    INJECTION_ERROR   INJECTION_BUFFER x RE_EMIT_ORDERED
        -> If(temporal_dominates, demote)
    GRANULARITY_ERROR COARSE_RECALL x EXPAND_GRANULARITY
        -> If(and(query_relevant, similarity_above 0.25), replace)

The translation is a claim about behavior, not a mechanical rewrite, so each
mapping is justified in `_ACTION_TRANSLATION` and each is asserted by a test.
Translating onto typed IR has a second benefit §6.4 requires: a closed spec and
a shallow-IR program then produce fingerprints through one `behavior_fingerprint`
channel, so the pre-search envelope union is a union of comparable values rather
than of two incommensurable encodings.

`SAFETY_ERROR` is excluded (§6.1: its historical eligibility path is
label-equivalent) and so are all five item actions (keyed by literal memory
IDs). That leaves the 3 actions this module enumerates.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterator

from cmd_audit.counterfactual.actions import (
    PIPELINE_ACTION_OPERATOR_DSL,
    PipelineAction,
)
from cmd_audit.counterfactual.program_ir import (
    Action,
    ActionKind,
    If,
    IR_GRAMMAR_VERSION,
    Predicate,
    PredicateKind,
    Program,
    Sequence,
    canonical_ast_hash,
    canonicalize,
    program_to_mapping,
)

__all__ = [
    "CLOSED_GRAMMAR_VERSION",
    "CLOSED_MAX_SEQUENCE_LENGTH",
    "CLOSED_ACTIONS",
    "EXCLUDED_ACTIONS",
    "ClosedGrammarSpec",
    "closed_grammar_manifest",
    "count_closed_grammar",
    "enumerate_closed_specs",
    "translate_action",
]

CLOSED_GRAMMAR_VERSION = "route-a-closed-grammar-v1"

#: §6.1 `sequence length: 0..3`.
CLOSED_MAX_SEQUENCE_LENGTH = 3

#: §6.1 `generation point: 0`.
CLOSED_GENERATION_POINT = 0

#: §6.1 non-safety, non-item legacy actions. Ordered, because the enumeration
#: order is what makes the artifact reproducible.
CLOSED_ACTIONS: tuple[PipelineAction, ...] = (
    PipelineAction.RETRIEVAL_ERROR,
    PipelineAction.INJECTION_ERROR,
    PipelineAction.GRANULARITY_ERROR,
)

#: Excluded by §6.1, with the reason, so the manifest can publish it rather than
#: leaving a reader to infer why the legacy action space shrank from 9 to 3.
EXCLUDED_ACTIONS: tuple[tuple[PipelineAction, str], ...] = (
    (
        PipelineAction.SAFETY_ERROR,
        "historical eligibility path is label-equivalent (§6.1)",
    ),
    (PipelineAction.ITEM_STALE, "keyed by literal memory IDs (§6.1)"),
    (PipelineAction.ITEM_CONFLICT, "keyed by literal memory IDs (§6.1)"),
    (PipelineAction.ITEM_POISONED, "keyed by literal memory IDs (§6.1)"),
    (PipelineAction.ITEM_WRONG, "keyed by literal memory IDs (§6.1)"),
    (
        PipelineAction.ITEM_COMPRESSION_DISTORTED,
        "keyed by literal memory IDs (§6.1)",
    ),
)


@dataclass(frozen=True)
class _Translation:
    """One legacy action's frozen selector/transform, as typed IR."""

    rule: If
    rationale: str


def _similarity(threshold: float) -> Predicate:
    return Predicate(kind=PredicateKind.SIMILARITY_ABOVE, threshold=threshold)


#: The §6.1 "frozen mapping for each action", carried onto the state surface.
#: Each rationale states which runtime signal stands in for the legacy
#: string-surface one, because that substitution is the only place this module
#: can silently diverge from the operator it claims to reproduce.
_ACTION_TRANSLATION: dict[PipelineAction, _Translation] = {
    PipelineAction.RETRIEVAL_ERROR: _Translation(
        rule=If(
            predicate=Predicate(kind=PredicateKind.EVIDENCE_MISSING),
            action=Action(ActionKind.RETRIEVE_FILL),
        ),
        rationale=(
            "MISSED_CANDIDATES x ADD_FROM_STORE. The legacy operator pulls pool "
            "items that are absent from recall; `retrieve_fill` is the only "
            "action that reads the unretrieved pool, and `evidence_missing` is "
            "the whole-case guard that fires exactly when recall does not cover "
            "the query -- the state-surface form of 'the retriever missed'."
        ),
    ),
    PipelineAction.INJECTION_ERROR: _Translation(
        rule=If(
            predicate=Predicate(kind=PredicateKind.TEMPORAL_DOMINATES),
            action=Action(ActionKind.DEMOTE),
        ),
        rationale=(
            "INJECTION_BUFFER x RE_EMIT_ORDERED. Re-emitting the buffer in order "
            "changes which of two same-slot items the model reads first. On "
            "state, ordering is expressed by disposition rather than by "
            "position, so demoting the later-ranked member of each same-slot "
            "pair is the transform that survives the surface change. It touches "
            "no pool item, matching the legacy operator's recall-only scope."
        ),
    ),
    PipelineAction.GRANULARITY_ERROR: _Translation(
        rule=If(
            predicate=Predicate(
                kind=PredicateKind.AND,
                operands=(
                    Predicate(kind=PredicateKind.QUERY_RELEVANT),
                    _similarity(0.25),
                ),
            ),
            action=Action(ActionKind.REPLACE),
        ),
        rationale=(
            "COARSE_RECALL x EXPAND_GRANULARITY. The legacy operator expands a "
            "coarse summary into its raw events. §8.2 forbids a literal in the "
            "AST, so `replace` cannot write the expanded text; it retires the "
            "coarse item to `historical` and leaves the detail to whatever else "
            "is in state. The selector is the state-surface reading of 'coarse': "
            "query-relevant and overlapping another recalled item, which is what "
            "a summary covering several events looks like without a "
            "`source_event_ids` count in the predicate vocabulary."
        ),
    ),
}


def translate_action(action: PipelineAction) -> If:
    """The frozen typed-IR rule for one legacy action.

    Raises on an action §6.1 excludes, so an excluded channel cannot enter the
    closed grammar by being passed in directly.
    """
    translation = _ACTION_TRANSLATION.get(action)
    if translation is None:
        raise ValueError(f"action is not in the sealed closed grammar: {action.value}")
    return translation.rule


@dataclass(frozen=True)
class ClosedGrammarSpec:
    """One legal legacy sequence: ordered single-action stages at point 0.

    Kept as a stage tuple rather than one `OperatorSpec` because §6.1 allows
    three actions at generation point 0 and `OperatorSpec.from_actions` rejects
    duplicate generation points.
    """

    actions: tuple[PipelineAction, ...]

    def __post_init__(self) -> None:
        if len(self.actions) > CLOSED_MAX_SEQUENCE_LENGTH:
            raise ValueError(
                f"sequence length {len(self.actions)} exceeds "
                f"{CLOSED_MAX_SEQUENCE_LENGTH}"
            )
        for action in self.actions:
            if action not in CLOSED_ACTIONS:
                raise ValueError(f"action outside the sealed grammar: {action.value}")

    @property
    def canonical_actions(self) -> tuple[PipelineAction, ...]:
        """§6.1 `duplicate action sequences: canonicalized`.

        Adjacent repeats collapse, matching `_canonical_rules`: re-running one
        action immediately is a no-op, so `A -> A` and `A` are one sequence. A
        repeat separated by another action is kept, because the second pass sees
        a state the first did not.
        """
        collapsed: list[PipelineAction] = []
        for action in self.actions:
            if collapsed and collapsed[-1] == action:
                continue
            collapsed.append(action)
        return tuple(collapsed)

    @property
    def program(self) -> Program:
        """The typed-IR program this sequence executes as.

        The empty sequence is the registered null program, so an E0 spec and an
        E0b program agree on what "do nothing" is.
        """
        rules = tuple(translate_action(action) for action in self.canonical_actions)
        if not rules:
            return Sequence(())
        if len(rules) == 1:
            return rules[0]
        return Sequence(rules)

    @property
    def canonical_program(self) -> Program:
        return canonicalize(self.program)

    def content_hash(self) -> str:
        """§6.1 dedup key: digest over the *canonicalized* stage list.

        Keying the raw list would make deduplication a no-op -- every generated
        sequence is already a distinct list -- and §6.2's raw-versus-canonical
        counts would be the same number reported twice.
        """
        payload = json.dumps(
            {
                "closed_grammar_version": CLOSED_GRAMMAR_VERSION,
                "generation_point": CLOSED_GENERATION_POINT,
                "stages": [action.value for action in self.canonical_actions],
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def canonical_ast_hash(self) -> str:
        return canonical_ast_hash(self.program)

    def format(self) -> str:
        if not self.canonical_actions:
            return "identity"
        return " -> ".join(action.value for action in self.canonical_actions)

    def as_mapping(self) -> dict[str, object]:
        return {
            "stages": [action.value for action in self.canonical_actions],
            "raw_stages": [action.value for action in self.actions],
            "sequence_length": len(self.canonical_actions),
            "content_hash": self.content_hash(),
            "canonical_ast_hash": self.canonical_ast_hash(),
            "program": program_to_mapping(self.canonical_program),
        }


def count_closed_grammar(max_length: int = CLOSED_MAX_SEQUENCE_LENGTH) -> int:
    """Raw generated count: every ordered sequence of length 0..max_length.

    Independent of the generator, so §6.2's "raw generated count" is checked
    rather than reported from the loop that produced it.
    """
    actions = len(CLOSED_ACTIONS)
    return sum(actions**length for length in range(max_length + 1))


def count_canonical_closed_grammar(
    max_length: int = CLOSED_MAX_SEQUENCE_LENGTH,
) -> int:
    """Analytic canonical-unique count: sequences with no adjacent repeat.

    Canonicalization collapses adjacent repeats, so each canonical class has
    exactly one representative with no adjacent repeat. There are `a` such
    sequences of length 1 and `a * (a-1)**(n-1)` of length n, plus the null
    program. Derived rather than counted so §6.2's canonical count is checked
    against arithmetic instead of against the loop that produced it.
    """
    actions = len(CLOSED_ACTIONS)
    total = 1 if max_length >= 0 else 0
    for length in range(1, max_length + 1):
        total += actions * (actions - 1) ** (length - 1)
    return total


def enumerate_closed_specs(
    max_length: int = CLOSED_MAX_SEQUENCE_LENGTH,
) -> Iterator[ClosedGrammarSpec]:
    """Yield every legal sequence, in fixed order, before canonicalization.

    Duplicates are yielded: §6.1 asks the enumerator to "generate every legal
    sequence, canonicalize it, and deduplicate by `content_hash`", and §6.2
    reports raw and canonical counts separately, so the raw stream has to exist
    for the difference between them to be a measurement.
    """
    for length in range(max_length + 1):
        for combination in itertools.product(CLOSED_ACTIONS, repeat=length):
            yield ClosedGrammarSpec(actions=combination)


@lru_cache(maxsize=None)
def _canonical_specs(max_length: int) -> tuple[ClosedGrammarSpec, ...]:
    """Content-hash-unique specs, first occurrence winning.

    Deduplication is by `content_hash` per §6.1. An adjacent repeat is a
    distinct stage list but canonicalizes to the same program, so the canonical
    AST hash is reported separately in the manifest rather than used as the key.
    """
    seen: set[str] = set()
    unique: list[ClosedGrammarSpec] = []
    for spec in enumerate_closed_specs(max_length):
        digest = spec.content_hash()
        if digest in seen:
            continue
        seen.add(digest)
        unique.append(spec)
    return tuple(unique)


def canonical_closed_specs(
    max_length: int = CLOSED_MAX_SEQUENCE_LENGTH,
) -> tuple[ClosedGrammarSpec, ...]:
    """§6.2 canonical unique specs, in enumeration order."""
    return _canonical_specs(max_length)


def closed_grammar_manifest(
    max_length: int = CLOSED_MAX_SEQUENCE_LENGTH,
) -> dict[str, object]:
    """§6.2 `closed_grammar_manifest.json`.

    Publishes the sealed grammar's shape and the frozen action translation, so a
    reader can check the state-surface mapping without reading this module.
    """
    specs = canonical_closed_specs(max_length)
    ast_hashes = {spec.canonical_ast_hash() for spec in specs}
    return {
        "closed_grammar_version": CLOSED_GRAMMAR_VERSION,
        "ir_grammar_version": IR_GRAMMAR_VERSION,
        "generation_point": CLOSED_GENERATION_POINT,
        "max_sequence_length": max_length,
        "action_count": len(CLOSED_ACTIONS),
        "actions": [action.value for action in CLOSED_ACTIONS],
        "excluded_actions": [
            {"action": action.value, "reason": reason}
            for action, reason in EXCLUDED_ACTIONS
        ],
        "literal_item_hints_permitted": False,
        "raw_generated_count": count_closed_grammar(max_length),
        "canonical_unique_count": len(specs),
        "distinct_canonical_ast_count": len(ast_hashes),
        "action_translation": {
            action.value: {
                "selector": PIPELINE_ACTION_OPERATOR_DSL[action].selector.value,
                "transform": PIPELINE_ACTION_OPERATOR_DSL[action].transform.value,
                "ir_predicate": program_to_mapping(
                    _ACTION_TRANSLATION[action].rule
                ),
                "rationale": _ACTION_TRANSLATION[action].rationale,
            }
            for action in CLOSED_ACTIONS
        },
        "translation_note": (
            "Legacy `apply_pipeline_action` edits a rendered context string; the "
            "E0 endpoint is measured on RepairState. Each action's frozen "
            "selector/transform is therefore re-expressed as typed IR on the "
            "state surface. The rewrite is behavioral, not mechanical: see each "
            "rationale, and note that `replace` cannot write expanded text "
            "because §8.2 forbids AST literals."
        ),
    }
