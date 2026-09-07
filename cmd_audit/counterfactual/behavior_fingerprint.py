"""Route A §8.3: neutral probe suite and behavioral identity.

An open synthesis run will emit many programs that differ only in syntax. If
those count as variation, the proposal ledger fills with rewrites of one
operator and the envelope reports novelty that does not exist. This module is
the discriminator: a frozen suite of 64 synthetic microcases, and a fingerprint
that is a hash over *observed behavior* across that suite rather than over the
AST.

Three properties are load-bearing.

The suite carries no case literal. Every probe is built from a closed synthetic
vocabulary (`PROBE_VOCABULARY`), item IDs are positional (`p0`, `p1`, ...), and
no dataset phrase, memory ID, family ID, or injector template marker appears
anywhere. A fingerprint therefore cannot leak a burned case into the ledger,
and a proposer that somehow read the suite learns nothing about tier-3 data.

Coverage is verified, not asserted. `coverage_matrix` decides whether a probe
exercises a predicate by *executing* that predicate against the probe through
the public executor, and whether it exercises an action by executing a rule
carrying it. A comment claiming "this probe covers CONTRADICTS" would rot the
first time the predicate's threshold moved; running it cannot.

Failure is behavior. A program that busts a case-dependent bound on some
probes still receives a fingerprint, with the exception recorded per probe.
Refusing to fingerprint it would mean a fail-closed operator and a working one
could not be told apart, which is exactly the confusion §8.3 exists to prevent.

Two definitions deserve stating because they are choices, not facts.

An action's *characteristic effect* differs by kind, so "applied" is checked
per kind: a state-hash change for the four disposition actions, `abstained` for
`Abstain`, a non-zero logical cost for `Verify`, a non-zero addition count for
`RetrieveFill`, and -- for `Keep`/`Preserve` -- being reached while leaving the
state byte-identical. Preservation *is* the identity actions' effect; demanding
a state change from them would assert something false about the grammar.

Predicate truth is read through `Verify`, whose logical cost equals the number
of matched items. That keeps coverage on the public API instead of reaching
into the executor's private matcher, and it means a cost-bound bust counts as
a match: the bound can only be exceeded by matching more items than it allows.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from cmd_audit.counterfactual.program_ir import (
    AGE_GAP_THRESHOLDS,
    SIMILARITY_THRESHOLDS,
    Action,
    ActionKind,
    IDENTITY_ACTION_KINDS,
    If,
    IdentityActionError,
    Predicate,
    PredicateKind,
    Program,
    ProgramBoundsError,
    Sequence,
)
from cmd_audit.counterfactual.repair_state import initial_state_from_runtime_case
from cmd_audit.counterfactual.state_executor import (
    ExecutionLimitError,
    execute_program,
)
from cmd_audit.eval.state_intent import (
    RuntimeEvent,
    RuntimeMemoryItem,
    RuntimeRepairCase,
)

__all__ = [
    "NEUTRAL_PROBE_COUNT",
    "PROBE_SUITE_VERSION",
    "PROBE_VOCABULARY",
    "REQUIRED_FAMILIES",
    "CoverageGap",
    "CoverageMatrix",
    "NeutralProbe",
    "behavior_fingerprint",
    "coverage_matrix",
    "deduplicate_by_behavior",
    "neutral_probe_suite",
    "probe_manifest",
    "probe_suite_sha256",
    "suite_sha256",
    "verify_coverage",
]

PROBE_SUITE_VERSION = "route-a-neutral-probes-v1"

#: §8.3. Frozen before any candidate fingerprint is observed, and recorded in
#: the preregistration as `registered_neutral_probe_count`.
NEUTRAL_PROBE_COUNT = 64

_GREEK = (
    "alpha",
    "beta",
    "gamma",
    "delta",
    "epsilon",
    "zeta",
    "eta",
    "theta",
    "iota",
    "kappa",
    "lambda",
    "mu",
    "nu",
    "xi",
    "omicron",
    "pi",
    "rho",
    "sigma",
    "tau",
    "upsilon",
    "phi",
    "chi",
    "psi",
    "omega",
)

_NEGATION = ("not", "never", "no")

#: The closed word list every probe is built from. A token outside it would be
#: a literal from somewhere, which is what §8.3 forbids.
PROBE_VOCABULARY = _GREEK + _NEGATION

REQUIRED_FAMILIES = (
    "predicate_boundary",
    "connective",
    "threshold_grid",
    "action_surface",
    "composition",
    "ordering",
    "token_boundary",
    "action_count_boundary",
    "logical_cost_boundary",
    "null_preservation",
    "fail_closed",
    "provenance",
    "mixed_store",
    "negation_polarity",
)

_RELIABLE = "verified"
_PLAIN = "default"


@dataclass(frozen=True)
class NeutralProbe:
    """One synthetic microcase. `note` records why the probe exists."""

    probe_id: str
    family: str
    case: RuntimeRepairCase
    note: str

    def as_mapping(self) -> dict[str, object]:
        return {
            "probe_id": self.probe_id,
            "family": self.family,
            "note": self.note,
            "query": self.case.query,
            "token_budget": self.case.token_budget,
            "items": [
                {
                    "item_id": item.item_id,
                    "text": item.text,
                    "store": item.store,
                    "rank": item.rank,
                    "retrieved": item.retrieved,
                    "source_event_ids": list(item.source_event_ids),
                }
                for item in self.case.items
            ],
            "raw_events": [
                {"event_id": event.event_id, "text": event.text}
                for event in self.case.raw_events
            ],
        }


@dataclass(frozen=True)
class CoverageMatrix:
    predicate_true: dict[str, int]
    predicate_false: dict[str, int]
    action_applied: dict[str, int]
    action_noop: dict[str, int]
    action_fail_closed: dict[str, int]
    threshold_values: dict[tuple[str, float], int]
    families: dict[str, int]

    def as_mapping(self) -> dict[str, object]:
        return {
            "predicate_true": dict(sorted(self.predicate_true.items())),
            "predicate_false": dict(sorted(self.predicate_false.items())),
            "action_applied": dict(sorted(self.action_applied.items())),
            "action_noop": dict(sorted(self.action_noop.items())),
            "action_fail_closed": dict(sorted(self.action_fail_closed.items())),
            "threshold_values": {
                f"{kind}@{value}": count
                for (kind, value), count in sorted(self.threshold_values.items())
            },
            "families": dict(sorted(self.families.items())),
        }


@dataclass(frozen=True)
class CoverageGap:
    """One unmet §8.3 coverage requirement."""

    requirement: str
    detail: str


# --------------------------------------------------------------------------
# Probe construction
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Spec:
    text: str
    store: str = _PLAIN
    retrieved: bool = True
    events: tuple[str, ...] = ()


def _probe(
    index: int,
    family: str,
    note: str,
    query: str,
    specs: tuple[_Spec, ...],
    *,
    known_events: tuple[str, ...] = (),
    token_budget: int = 256,
) -> NeutralProbe:
    probe_id = f"probe_{index:02d}"
    items = tuple(
        RuntimeMemoryItem(
            item_id=f"p{position}",
            text=spec.text,
            source_event_ids=spec.events,
            store=spec.store,
            rank=position,
            retrieved=spec.retrieved,
        )
        for position, spec in enumerate(specs)
    )
    return NeutralProbe(
        probe_id=probe_id,
        family=family,
        note=note,
        case=RuntimeRepairCase(
            case_id=probe_id,
            family_id=f"probe_family_{family}",
            query=query,
            items=items,
            raw_events=tuple(
                # Event text is never read by a predicate; only the ID set is.
                RuntimeEvent(event_id=event_id, text="alpha")
                for event_id in known_events
            ),
            token_budget=token_budget,
        ),
    )


def _series(count: int, *, store: str = _PLAIN, retrieved: bool = True) -> tuple[_Spec, ...]:
    """`count` items with low mutual overlap, for rank and cardinality probes."""
    return tuple(
        _Spec(
            text=f"{_GREEK[position % len(_GREEK)]} {_GREEK[(position * 7 + 3) % len(_GREEK)]}",
            store=store,
            retrieved=retrieved,
        )
        for position in range(count)
    )


def _build_suite() -> tuple[NeutralProbe, ...]:
    """The frozen 64. Ordered by family; index is the probe's identity."""
    long_text = " ".join(_GREEK * 25)  # 600 tokens: over max_token_delta.
    medium_text = " ".join(_GREEK * 8)  # 192 tokens: under it.
    rows: list[tuple[str, str, str, tuple[_Spec, ...], dict[str, object]]] = [
        # -- predicate_boundary: each leaf predicate, matching and not (16) --
        (
            "predicate_boundary",
            "query_relevant fires on a shared query token",
            "alpha beta",
            (_Spec("alpha gamma"), _Spec("delta epsilon")),
            {},
        ),
        (
            "predicate_boundary",
            "query_relevant matches nothing when the query is disjoint",
            "omega psi",
            (_Spec("alpha beta"), _Spec("gamma delta")),
            {},
        ),
        (
            "predicate_boundary",
            "temporal_dominates names the later-ranked member of a same-slot pair",
            "alpha",
            (_Spec("alpha beta gamma"), _Spec("alpha beta delta")),
            {},
        ),
        (
            "predicate_boundary",
            "temporal_dominates is empty without a same-slot pair",
            "alpha",
            (_Spec("alpha beta"), _Spec("gamma delta")),
            {},
        ),
        (
            "predicate_boundary",
            "contradicts fires on opposite polarity over shared content",
            "alpha",
            (_Spec("alpha beta gamma"), _Spec("not alpha beta gamma")),
            {},
        ),
        (
            "predicate_boundary",
            "contradicts is empty when overlapping items agree in polarity",
            "alpha",
            (_Spec("alpha beta gamma"), _Spec("alpha beta delta")),
            {},
        ),
        (
            "predicate_boundary",
            "source_more_reliable fires on items a reliable sibling outranks",
            "alpha",
            (_Spec("alpha", store=_RELIABLE), _Spec("beta")),
            {},
        ),
        (
            "predicate_boundary",
            "source_more_reliable is empty with no reliable item present",
            "alpha",
            (_Spec("alpha"), _Spec("beta")),
            {},
        ),
        (
            "predicate_boundary",
            "provenance_matches fires on a known source event",
            "alpha",
            (_Spec("alpha", events=("e1",)), _Spec("beta")),
            {"known_events": ("e1", "e2")},
        ),
        (
            "predicate_boundary",
            "provenance_matches is empty when the source event is unknown",
            "alpha",
            (_Spec("alpha", events=("e9",)), _Spec("beta")),
            {"known_events": ("e1",)},
        ),
        (
            "predicate_boundary",
            "similarity_above fires on a half-overlapping pair",
            "alpha",
            (_Spec("alpha beta"), _Spec("alpha beta gamma delta")),
            {},
        ),
        (
            "predicate_boundary",
            "similarity_above is empty on disjoint items",
            "alpha",
            (_Spec("alpha"), _Spec("beta")),
            {},
        ),
        (
            "predicate_boundary",
            "age_gap_above fires on an item one rank below the top",
            "alpha",
            (_Spec("alpha"), _Spec("beta")),
            {},
        ),
        (
            "predicate_boundary",
            "age_gap_above is empty when only the top rank exists",
            "alpha",
            (_Spec("alpha"),),
            {},
        ),
        (
            "predicate_boundary",
            "evidence_missing fires when recall covers none of the query",
            "omega psi chi",
            (_Spec("alpha beta"),),
            {},
        ),
        (
            "predicate_boundary",
            "evidence_missing is empty when recall covers the query",
            "alpha beta",
            (_Spec("alpha beta gamma"),),
            {},
        ),
        # -- connective: and / or / not, matching and not (6) --
        (
            "connective",
            "and fires only where both operands hold",
            "alpha",
            (_Spec("alpha beta gamma"), _Spec("not alpha beta gamma")),
            {},
        ),
        (
            "connective",
            "and is empty when one operand is empty",
            "omega",
            (_Spec("alpha beta gamma"), _Spec("not alpha beta gamma")),
            {},
        ),
        (
            "connective",
            "or fires from either operand",
            "alpha",
            (_Spec("alpha", store=_RELIABLE, events=("e1",)), _Spec("beta")),
            {"known_events": ("e1",)},
        ),
        (
            "connective",
            "or is empty when both operands are empty",
            "alpha",
            (_Spec("alpha"), _Spec("beta")),
            {},
        ),
        (
            "connective",
            "not fires on the complement of an empty inner predicate",
            "alpha",
            (_Spec("alpha beta"), _Spec("gamma delta")),
            {},
        ),
        (
            "connective",
            "not is empty when the inner predicate takes every item",
            "alpha",
            (_Spec("alpha beta gamma"), _Spec("not alpha beta gamma")),
            {},
        ),
        # -- threshold_grid: every registered threshold value (6) --
        (
            "threshold_grid",
            "pair overlapping at exactly the lowest similarity threshold",
            "alpha",
            (_Spec("alpha beta"), _Spec("alpha gamma delta")),
            {},
        ),
        (
            "threshold_grid",
            "pair overlapping at exactly the middle similarity threshold",
            "alpha",
            (_Spec("alpha beta"), _Spec("alpha beta gamma delta")),
            {},
        ),
        (
            "threshold_grid",
            "pair overlapping at exactly the highest similarity threshold",
            "alpha",
            (_Spec("alpha beta gamma"), _Spec("alpha beta gamma delta")),
            {},
        ),
        (
            "threshold_grid",
            "recall deep enough to reach the smallest age gap",
            "omega",
            _series(2),
            {},
        ),
        (
            "threshold_grid",
            "recall deep enough to reach the middle age gap",
            "omega",
            _series(8),
            {},
        ),
        (
            "threshold_grid",
            "recall deep enough to reach the largest age gap",
            "omega",
            _series(32),
            {},
        ),
        # -- action_surface: each action's characteristic effect (9) --
        (
            "action_surface",
            "keep reached on a relevant item leaves state byte-identical",
            "alpha",
            (_Spec("alpha beta"),),
            {},
        ),
        (
            "action_surface",
            "preserve reached on a relevant item leaves state byte-identical",
            "alpha",
            (_Spec("alpha gamma"), _Spec("delta")),
            {},
        ),
        (
            "action_surface",
            "demote moves a relevant item to the lower section",
            "alpha",
            (_Spec("alpha delta"), _Spec("epsilon")),
            {},
        ),
        (
            "action_surface",
            "suppress withholds a relevant item from the context",
            "alpha",
            (_Spec("alpha epsilon"), _Spec("zeta")),
            {},
        ),
        (
            "action_surface",
            "replace retires a relevant item to historical",
            "alpha",
            (_Spec("alpha zeta"), _Spec("eta")),
            {},
        ),
        (
            "action_surface",
            "annotate_conflict marks a relevant item as conflicting",
            "alpha",
            (_Spec("alpha eta"), _Spec("theta")),
            {},
        ),
        (
            "action_surface",
            "retrieve_fill pulls a relevant candidate out of the pool",
            "alpha beta",
            (_Spec("alpha"), _Spec("beta gamma", retrieved=False)),
            {},
        ),
        (
            "action_surface",
            "abstain records abstention without touching state",
            "alpha",
            (_Spec("alpha theta"), _Spec("iota")),
            {},
        ),
        (
            "action_surface",
            "verify spends logical cost without touching state",
            "alpha",
            (_Spec("alpha iota"), _Spec("kappa")),
            {},
        ),
        # -- composition: several primitives interacting in one state (4) --
        (
            "composition",
            "contradiction pair alongside an uncovered query and a pool candidate",
            "omega psi",
            (
                _Spec("alpha beta gamma"),
                _Spec("not alpha beta gamma"),
                _Spec("omega psi", retrieved=False),
            ),
            {},
        ),
        (
            "composition",
            "reliable and unreliable siblings that also form a same-slot pair",
            "alpha",
            (
                _Spec("alpha beta gamma", store=_RELIABLE),
                _Spec("alpha beta delta"),
            ),
            {},
        ),
        (
            "composition",
            "provenance-bearing item inside a contradiction pair",
            "alpha",
            (
                _Spec("alpha beta gamma", events=("e1",)),
                _Spec("not alpha beta gamma", events=("e2",)),
            ),
            {"known_events": ("e1", "e2")},
        ),
        (
            "composition",
            "deep recall where similarity and age gap both fire",
            "alpha",
            (
                _Spec("alpha beta gamma"),
                _Spec("alpha beta gamma delta"),
                _Spec("epsilon zeta"),
                _Spec("eta theta"),
            ),
            {},
        ),
        # -- ordering: a fill changes what a later rule can see (3) --
        (
            "ordering",
            "pool candidate that becomes a same-slot partner once filled",
            "omega",
            (
                _Spec("alpha beta gamma"),
                _Spec("omega alpha beta gamma", retrieved=False),
            ),
            {},
        ),
        (
            "ordering",
            "pool candidate that becomes a contradiction partner once filled",
            "omega",
            (
                _Spec("alpha beta gamma"),
                _Spec("omega not alpha beta gamma", retrieved=False),
            ),
            {},
        ),
        (
            "ordering",
            "two pool candidates whose arrival order changes rank assignment",
            "omega psi",
            (
                _Spec("alpha"),
                _Spec("omega alpha", retrieved=False),
                _Spec("psi alpha", retrieved=False),
            ),
            {},
        ),
        # -- token_boundary: the max_token_delta edge (3) --
        (
            "token_boundary",
            "pool candidate far larger than the registered token delta",
            "omega psi",
            (_Spec("alpha"), _Spec(f"omega {long_text}", retrieved=False)),
            {"token_budget": 1024},
        ),
        (
            "token_boundary",
            "pool candidate comfortably inside the registered token delta",
            "omega psi",
            (_Spec("alpha"), _Spec(f"omega {medium_text}", retrieved=False)),
            {"token_budget": 1024},
        ),
        (
            "token_boundary",
            "pool candidate of a single token",
            "omega psi",
            (_Spec("alpha"), _Spec("omega", retrieved=False)),
            {"token_budget": 8},
        ),
        # -- action_count_boundary: the max_retrieved_additions edge (3) --
        (
            "action_count_boundary",
            "pool of exactly the registered addition allowance",
            "omega",
            (_Spec("alpha"),) + _series(4, retrieved=False),
            {},
        ),
        (
            "action_count_boundary",
            "pool one candidate over the registered addition allowance",
            "omega",
            (_Spec("alpha"),) + _series(5, retrieved=False),
            {},
        ),
        (
            "action_count_boundary",
            "pool of a single candidate",
            "omega",
            (_Spec("alpha"), _Spec("omega beta", retrieved=False)),
            {},
        ),
        # -- logical_cost_boundary: the max_logical_cost edge (3) --
        (
            "logical_cost_boundary",
            "recall half the registered logical cost allowance",
            "alpha",
            tuple(_Spec(f"alpha {word}") for word in _GREEK[:8]),
            {},
        ),
        (
            "logical_cost_boundary",
            "recall exactly the registered logical cost allowance",
            "alpha",
            tuple(_Spec(f"alpha {word}") for word in _GREEK[:16]),
            {},
        ),
        (
            "logical_cost_boundary",
            "recall one item over the registered logical cost allowance",
            "alpha",
            tuple(_Spec(f"alpha {word}") for word in _GREEK[:17]),
            {},
        ),
        # -- null_preservation: nothing to repair (2) --
        (
            "null_preservation",
            "single reliable item already covering the query",
            "alpha beta",
            (_Spec("alpha beta", store=_RELIABLE, events=("e1",)),),
            {"known_events": ("e1",)},
        ),
        (
            "null_preservation",
            "two agreeing reliable items with no pool behind them",
            "alpha gamma",
            (
                _Spec("alpha gamma", store=_RELIABLE, events=("e1",)),
                _Spec("delta epsilon", store="source", events=("e2",)),
            ),
            {"known_events": ("e1", "e2")},
        ),
        # -- fail_closed: a greedy program must bust a bound here (2) --
        (
            "fail_closed",
            "uncovered query behind a pool larger than the addition allowance",
            "omega psi chi",
            (_Spec("alpha beta"),) + tuple(
                _Spec(f"omega {word}", retrieved=False) for word in _GREEK[:6]
            ),
            {},
        ),
        (
            "fail_closed",
            "uncovered query behind a pool candidate over the token delta",
            "omega psi chi",
            (
                _Spec("alpha beta"),
                _Spec(f"omega {long_text}", retrieved=False),
            ),
            {"token_budget": 1024},
        ),
        # -- provenance: subset and mixed source-event sets (2) --
        (
            "provenance",
            "item whose source events are a strict subset of the known set",
            "alpha",
            (_Spec("alpha beta", events=("e1",)), _Spec("gamma", events=("e2",))),
            {"known_events": ("e1", "e2", "e3")},
        ),
        (
            "provenance",
            "item mixing a known and an unknown source event",
            "alpha",
            (
                _Spec("alpha beta", events=("e1", "e9")),
                _Spec("gamma", events=("e2",)),
            ),
            {"known_events": ("e1", "e2")},
        ),
        # -- mixed_store: reliability at different ranks (3) --
        (
            "mixed_store",
            "reliable item ranked below two plain siblings",
            "alpha",
            (_Spec("alpha beta"), _Spec("gamma"), _Spec("delta", store=_RELIABLE)),
            {},
        ),
        (
            "mixed_store",
            "every item drawn from a reliable store",
            "alpha",
            (
                _Spec("alpha beta", store=_RELIABLE),
                _Spec("gamma", store="document"),
                _Spec("delta", store="tool"),
            ),
            {},
        ),
        (
            "mixed_store",
            "reliable item contradicting a plain sibling",
            "alpha",
            (
                _Spec("alpha beta gamma", store=_RELIABLE),
                _Spec("not alpha beta gamma"),
            ),
            {},
        ),
        # -- negation_polarity: the polarity half of contradicts (2) --
        (
            "negation_polarity",
            "high overlap with both items negated, so polarity agrees",
            "alpha",
            (_Spec("not alpha beta gamma"), _Spec("never alpha beta gamma")),
            {},
        ),
        (
            "negation_polarity",
            "opposite polarity over disjoint content, so no shared slot",
            "alpha",
            (_Spec("alpha beta"), _Spec("not gamma delta")),
            {},
        ),
    ]
    return tuple(
        _probe(index, family, note, query, specs, **kwargs)  # type: ignore[arg-type]
        for index, (family, note, query, specs, kwargs) in enumerate(rows)
    )


@lru_cache(maxsize=1)
def neutral_probe_suite() -> tuple[NeutralProbe, ...]:
    """The frozen suite. Exactly `NEUTRAL_PROBE_COUNT` probes."""
    suite = _build_suite()
    if len(suite) != NEUTRAL_PROBE_COUNT:
        raise AssertionError(
            f"probe suite has {len(suite)} probes, registered count is "
            f"{NEUTRAL_PROBE_COUNT}"
        )
    return suite


def _suite_payload(suite: tuple[NeutralProbe, ...]) -> str:
    return json.dumps(
        {
            "probe_suite_version": PROBE_SUITE_VERSION,
            "probes": [probe.as_mapping() for probe in suite],
        },
        sort_keys=True,
    )


def suite_sha256(suite: tuple[NeutralProbe, ...]) -> str:
    """Digest over a serialized suite, including per-item pool membership.

    `retrieved` is inside the digest deliberately: moving one probe item between
    recall and the candidate pool changes what every retrieval predicate sees,
    so a digest blind to it would let the suite change under a frozen hash.
    """
    return hashlib.sha256(_suite_payload(suite).encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def probe_suite_sha256() -> str:
    """Digest of the frozen suite."""
    return suite_sha256(neutral_probe_suite())


# --------------------------------------------------------------------------
# Behavior observation
# --------------------------------------------------------------------------

_VERIFY = Action(ActionKind.VERIFY)


def _observe(program: Program, probe: NeutralProbe) -> dict[str, object]:
    """Run `program` on one probe. An exception is an observation, not a stop."""
    state = initial_state_from_runtime_case(probe.case)
    try:
        result = execute_program(program, probe.case, state)
    except (ExecutionLimitError, ProgramBoundsError, IdentityActionError) as error:
        return {"error": type(error).__name__}
    return {
        "state_hash": result.state.state_hash,
        "matched_item_count": result.matched_item_count,
        "retrieved_additions": result.retrieved_additions,
        "token_delta": result.token_delta,
        "logical_cost": result.logical_cost,
        "abstained": result.abstained,
        "fired_rules": result.fired_rules,
        "dispositions": [
            [item.item_id, item.disposition] for item in result.state.items
        ],
    }


def behavior_fingerprint(
    program: Program, suite: tuple[NeutralProbe, ...] | None = None
) -> str:
    """SHA-256 over the program's observed behavior on the frozen suite.

    Two programs share a fingerprint exactly when nothing in the suite can tell
    them apart, which is the §8.3 definition of "not a variation".

    The suite digest is hashed in alongside the observations, so a fingerprint
    from one suite can never compare equal to a fingerprint from another. §8.3
    forbids changing the suite once a candidate has been fingerprinted; this
    makes a violation show up as universal mismatch rather than silent drift.
    """
    probes = neutral_probe_suite() if suite is None else suite
    payload = json.dumps(
        {
            "probe_suite_version": PROBE_SUITE_VERSION,
            "suite_sha256": (
                probe_suite_sha256() if suite is None else suite_sha256(probes)
            ),
            "observations": [
                [probe.probe_id, _observe(program, probe)] for probe in probes
            ],
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def deduplicate_by_behavior(
    programs: list[Program], suite: tuple[NeutralProbe, ...] | None = None
) -> list[Program]:
    """Keep the first program of each behavior class, in the given order."""
    seen: set[str] = set()
    kept: list[Program] = []
    for program in programs:
        fingerprint = behavior_fingerprint(program, suite)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        kept.append(program)
    return kept


# --------------------------------------------------------------------------
# Coverage, measured by execution
# --------------------------------------------------------------------------

_RELEVANT = Predicate(kind=PredicateKind.QUERY_RELEVANT)
_CONTRADICTS = Predicate(kind=PredicateKind.CONTRADICTS)
_RELIABLE_SOURCE = Predicate(kind=PredicateKind.SOURCE_MORE_RELIABLE)
_PROVENANCE = Predicate(kind=PredicateKind.PROVENANCE_MATCHES)

#: Representative form for each connective. A connective has no truth of its
#: own, so coverage is measured through one registered instantiation.
_CONNECTIVE_PROBES = {
    PredicateKind.AND: Predicate(
        kind=PredicateKind.AND, operands=(_RELEVANT, _CONTRADICTS)
    ),
    PredicateKind.OR: Predicate(
        kind=PredicateKind.OR, operands=(_RELIABLE_SOURCE, _PROVENANCE)
    ),
    PredicateKind.NOT: Predicate(kind=PredicateKind.NOT, operands=(_CONTRADICTS,)),
}


def _leaf_probe_predicate(kind: PredicateKind) -> Predicate:
    if kind in _CONNECTIVE_PROBES:
        return _CONNECTIVE_PROBES[kind]
    if kind is PredicateKind.SIMILARITY_ABOVE:
        return Predicate(kind=kind, threshold=SIMILARITY_THRESHOLDS[0])
    if kind is PredicateKind.AGE_GAP_ABOVE:
        return Predicate(kind=kind, threshold=AGE_GAP_THRESHOLDS[0])
    return Predicate(kind=kind)


def _predicate_matches(predicate: Predicate, probe: NeutralProbe) -> bool:
    """Whether `predicate` selects at least one item on `probe`.

    Read through `Verify`, whose logical cost is the matched-item count, so this
    stays on the public executor API. A cost-bound bust therefore means the
    predicate matched more items than the bound allows -- still a match.
    """
    observation = _observe(If(predicate=predicate, action=_VERIFY), probe)
    if "error" in observation:
        return observation["error"] == "ExecutionLimitError"
    return int(observation["logical_cost"]) > 0


def _action_effect(kind: ActionKind, probe: NeutralProbe) -> str:
    """`applied`, `noop`, or `fail_closed` for one action on one probe.

    Anchored on `query_relevant` so every action is judged against the same
    reachability condition; see the module docstring on what "applied" means
    for each kind.
    """
    if not _predicate_matches(_RELEVANT, probe):
        return "noop"
    rule = If(predicate=_RELEVANT, action=Action(kind))
    if kind in IDENTITY_ACTION_KINDS:
        # An identity action cannot stand alone (canonicalization drops it), so
        # it is observed by padding a rule that does act and checking that the
        # padded and unpadded programs land on the same state.
        padded = _observe(Sequence((rule, If(predicate=_RELEVANT, action=_VERIFY))), probe)
        plain = _observe(If(predicate=_RELEVANT, action=_VERIFY), probe)
        if "error" in padded or "error" in plain:
            return "fail_closed"
        return "applied" if padded["state_hash"] == plain["state_hash"] else "noop"

    observation = _observe(rule, probe)
    if "error" in observation:
        return "fail_closed"
    if kind is ActionKind.ABSTAIN:
        return "applied" if observation["abstained"] else "noop"
    if kind is ActionKind.VERIFY:
        return "applied" if int(observation["logical_cost"]) > 0 else "noop"
    if kind is ActionKind.RETRIEVE_FILL:
        return "applied" if int(observation["retrieved_additions"]) > 0 else "noop"
    baseline = initial_state_from_runtime_case(probe.case).state_hash
    return "applied" if observation["state_hash"] != baseline else "noop"


def _coverage_matrix(suite: tuple[NeutralProbe, ...]) -> CoverageMatrix:
    predicate_true = {kind.value: 0 for kind in PredicateKind}
    predicate_false = {kind.value: 0 for kind in PredicateKind}
    action_applied = {kind.value: 0 for kind in ActionKind}
    action_noop = {kind.value: 0 for kind in ActionKind}
    action_fail_closed = {kind.value: 0 for kind in ActionKind}
    threshold_values: dict[tuple[str, float], int] = {
        ("similarity_above", value): 0 for value in SIMILARITY_THRESHOLDS
    }
    threshold_values.update(
        {("age_gap_above", value): 0 for value in AGE_GAP_THRESHOLDS}
    )
    families: dict[str, int] = {}

    for probe in suite:
        families[probe.family] = families.get(probe.family, 0) + 1
        for kind in PredicateKind:
            if _predicate_matches(_leaf_probe_predicate(kind), probe):
                predicate_true[kind.value] += 1
            else:
                predicate_false[kind.value] += 1
        for kind in ActionKind:
            effect = _action_effect(kind, probe)
            if effect == "applied":
                action_applied[kind.value] += 1
            elif effect == "noop":
                action_noop[kind.value] += 1
            else:
                action_fail_closed[kind.value] += 1
        for value in SIMILARITY_THRESHOLDS:
            predicate = Predicate(
                kind=PredicateKind.SIMILARITY_ABOVE, threshold=value
            )
            if _predicate_matches(predicate, probe):
                threshold_values[("similarity_above", value)] += 1
        for value in AGE_GAP_THRESHOLDS:
            predicate = Predicate(kind=PredicateKind.AGE_GAP_ABOVE, threshold=value)
            if _predicate_matches(predicate, probe):
                threshold_values[("age_gap_above", value)] += 1

    return CoverageMatrix(
        predicate_true=predicate_true,
        predicate_false=predicate_false,
        action_applied=action_applied,
        action_noop=action_noop,
        action_fail_closed=action_fail_closed,
        threshold_values=threshold_values,
        families=families,
    )


@lru_cache(maxsize=1)
def _frozen_coverage() -> CoverageMatrix:
    return _coverage_matrix(neutral_probe_suite())


def coverage_matrix(
    suite: tuple[NeutralProbe, ...] | None = None,
) -> CoverageMatrix:
    """Per-primitive coverage, decided by running the suite."""
    if suite is None:
        return _frozen_coverage()
    return _coverage_matrix(suite)


def verify_coverage(
    suite: tuple[NeutralProbe, ...] | None = None,
) -> tuple[CoverageGap, ...]:
    """Every §8.3 coverage requirement that the suite fails to meet."""
    matrix = coverage_matrix(suite)
    gaps: list[CoverageGap] = []
    for kind in PredicateKind:
        if matrix.predicate_true[kind.value] < 1:
            gaps.append(CoverageGap("predicate_true", kind.value))
        if matrix.predicate_false[kind.value] < 1:
            gaps.append(CoverageGap("predicate_false", kind.value))
    for kind in ActionKind:
        if matrix.action_applied[kind.value] < 1:
            gaps.append(CoverageGap("action_applied", kind.value))
        if matrix.action_noop[kind.value] < 1:
            gaps.append(CoverageGap("action_noop", kind.value))
    for key, count in sorted(matrix.threshold_values.items()):
        if count < 1:
            gaps.append(CoverageGap("threshold_value", f"{key[0]}@{key[1]}"))
    for family in REQUIRED_FAMILIES:
        if matrix.families.get(family, 0) < 1:
            gaps.append(CoverageGap("family", family))
    return tuple(gaps)


@lru_cache(maxsize=1)
def _construction_code_sha256() -> str:
    """Digest of this module's source, so the manifest pins how the suite was built."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def probe_manifest() -> dict[str, object]:
    """The §8.3 manifest, frozen before any candidate fingerprint is observed."""
    return {
        "probe_suite_version": PROBE_SUITE_VERSION,
        "probe_count": len(neutral_probe_suite()),
        "suite_sha256": probe_suite_sha256(),
        "construction_code_sha256": _construction_code_sha256(),
        "vocabulary_size": len(PROBE_VOCABULARY),
        "families": list(REQUIRED_FAMILIES),
        "coverage": coverage_matrix().as_mapping(),
        "coverage_gaps": [
            {"requirement": gap.requirement, "detail": gap.detail}
            for gap in verify_coverage()
        ],
    }
