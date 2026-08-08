"""The successor grammar: v1 plus one leaf, without touching v1.

`slot_divergence` measures 1200/1200 on `stale_item` where `CONTRADICTS`
measures 0/1200. To be worth anything it has to reach the search, and the search
reads `PredicateKind`. But `IR_GRAMMAR_VERSION = "route-a-ir-v1"` is frozen:
E0's artifacts carry that string, and §11.3's 5482-class envelope was enumerated
over exactly those nine leaves. Adding a tenth member to `PredicateKind` would
leave every existing artifact naming a grammar that no longer exists.

So the successor is a *separate* vocabulary that delegates. The delegation rests
on one structural fact, which the tests below pin because the whole design
depends on it: **v1's `_match_predicate` recurses only through connectives**
(`and`/`or`/`not`), and a v1 leaf carries no operands. If v2 handles its own
connectives and hands down only leaves, v1 code can never receive a v2 kind. It
is not a bypass of the freeze -- v1 evaluates exactly the programs it always
did, and its version string keeps meaning what it meant.

What this must NOT become: a second place where a threshold gets tuned. The new
leaf takes no parameter. `SLOT_DIVERGES` either sees a same-slot,
different-value disagreement or it does not, and the sensor that decides is
already frozen behind its own version string and its own test suite.
"""

import unittest

from cmd_audit.counterfactual.program_ir import (
    IR_GRAMMAR_VERSION,
    LEAF_PREDICATE_KINDS,
    Action,
    ActionKind,
    If,
    Predicate,
    PredicateKind,
    Sequence,
)
from cmd_audit.counterfactual.repair_state import RepairStateItem
from cmd_audit.counterfactual.slot_divergence import SLOT_DIVERGENCE_VERSION
from cmd_audit.counterfactual.successor_grammar import (
    SUCCESSOR_GRAMMAR_VERSION,
    SUCCESSOR_SENSOR_VERSIONS,
    SuccessorPredicate,
    SuccessorPredicateKind,
    match_successor_predicate,
    parse_successor_predicate,
    successor_leaf_kinds,
)


def _state_item(item_id: str, text: str, rank: int = 0, store: str | None = None):
    return RepairStateItem(
        item_id=item_id,
        text=text,
        source_event_ids=(),
        store=f"2026-01-{rank + 1:02d}T00:00:00Z" if store is None else store,
        provenance_hash="",
        rank=rank,
        disposition="active",
    )


STALE_TEXT = "M_old: I've been based in Seattle for the last few years."
CURRENT_TEXT = (
    "M_new: I just finished updating my address after settling into my new "
    "place in Austin, and I’m trying to get set up with the local utilities."
)


class _Case:
    """The runtime read surface `_match_predicate` needs, and nothing else."""

    def __init__(self, query: str = "where do I live"):
        self.case_id = "c1"
        self.query = query
        self.raw_events = ()
        self.gold_answer = None


class FreezeIsIntactTest(unittest.TestCase):
    """The successor exists so that v1 does not have to change."""

    def test_v1_grammar_version_is_unchanged(self) -> None:
        self.assertEqual(IR_GRAMMAR_VERSION, "route-a-ir-v1")

    def test_v1_predicate_vocabulary_still_has_exactly_eight_leaves(self) -> None:
        """§11.3's 5482-class envelope was enumerated over these eight, and
        §8.1's frozen grammar block lists exactly them. A ninth member would
        silently invalidate that count in every artifact that cites it.

        (`PredicateKind`'s own docstring says "nine leaf tests". The spec block
        and this enum both say eight; the docstring is off by one.)
        """
        self.assertEqual(
            [kind.value for kind in LEAF_PREDICATE_KINDS],
            [
                "query_relevant",
                "temporal_dominates",
                "contradicts",
                "source_more_reliable",
                "provenance_matches",
                "similarity_above",
                "age_gap_above",
                "evidence_missing",
            ],
        )

    def test_the_successor_version_is_distinguishable(self) -> None:
        self.assertNotEqual(SUCCESSOR_GRAMMAR_VERSION, IR_GRAMMAR_VERSION)
        self.assertTrue(SUCCESSOR_GRAMMAR_VERSION)


class VocabularyTest(unittest.TestCase):
    def test_every_v1_leaf_is_still_expressible(self) -> None:
        """A successor that dropped a leaf would not be a superset, and the
        E0 winner could not be re-run under it for comparison."""
        v1_leaves = {kind.value for kind in LEAF_PREDICATE_KINDS}
        successor = {kind.value for kind in successor_leaf_kinds()}
        self.assertTrue(v1_leaves <= successor, v1_leaves - successor)

    def test_the_new_leaf_is_the_only_addition(self) -> None:
        v1_leaves = {kind.value for kind in LEAF_PREDICATE_KINDS}
        added = {kind.value for kind in successor_leaf_kinds()} - v1_leaves
        self.assertEqual(added, {"slot_diverges"})

    def test_the_new_leaf_takes_no_threshold(self) -> None:
        """The sensor has no free parameter, and this is the one place a
        threshold could be smuggled back in."""
        with self.assertRaises(ValueError):
            SuccessorPredicate(
                kind=SuccessorPredicateKind.SLOT_DIVERGES,
                threshold=0.3,
            )

    def test_no_leaf_accepts_operands(self) -> None:
        """The load-bearing structural check.

        A leaf carrying operands is how a v2 subtree would reach v1: the leaf
        gets delegated, `_to_v1_leaf` drops the operands, and the v2 kind
        underneath is silently discarded rather than evaluated. Refusing at
        construction is what makes "v1 only ever sees leaves" a property of the
        type rather than a property of the current call graph.
        """
        for kind in successor_leaf_kinds():
            with self.subTest(kind=kind.value):
                with self.assertRaises(ValueError):
                    SuccessorPredicate(
                        kind=kind,
                        operands=(
                            SuccessorPredicate(
                                kind=SuccessorPredicateKind.SLOT_DIVERGES
                            ),
                        ),
                        threshold=(
                            0.5
                            if kind
                            in (
                                SuccessorPredicateKind.SIMILARITY_ABOVE,
                                SuccessorPredicateKind.AGE_GAP_ABOVE,
                            )
                            else None
                        ),
                    )

    def test_not_takes_exactly_one_operand(self) -> None:
        """`not` with two operands has no defined meaning here, and v1 reads
        `operands[0]` -- so admitting it would silently evaluate the first and
        drop the rest."""
        one = SuccessorPredicate(kind=SuccessorPredicateKind.SLOT_DIVERGES)
        for count in (0, 2, 3):
            with self.subTest(operands=count):
                with self.assertRaises(ValueError):
                    SuccessorPredicate(
                        kind=SuccessorPredicateKind.NOT,
                        operands=(one,) * count,
                    )

    def test_a_predicate_is_frozen(self) -> None:
        """It is recorded in a proposal ledger; a caller mutating one would
        change what the search reported after the fact."""
        predicate = SuccessorPredicate(
            kind=SuccessorPredicateKind.SLOT_DIVERGES
        )
        with self.assertRaises(Exception):
            predicate.kind = SuccessorPredicateKind.CONTRADICTS  # type: ignore[misc]

    def test_the_grammar_records_which_sensor_decides(self) -> None:
        """`route-a-ir-v2-slot` means nothing without the sensor version behind
        its new leaf: two runs naming this grammar but running different
        `slot_divergence` builds would not be comparable, and an artifact
        carrying only the grammar string could not tell them apart."""
        self.assertEqual(
            SUCCESSOR_SENSOR_VERSIONS["slot_diverges"],
            SLOT_DIVERGENCE_VERSION,
        )
        self.assertTrue(SLOT_DIVERGENCE_VERSION)


class DelegationTest(unittest.TestCase):
    """The structural fact the whole design rests on."""

    def test_a_v1_leaf_is_evaluated_by_v1(self) -> None:
        """Delegation, not reimplementation: a second copy of
        `EVIDENCE_MISSING` would be a second thing to keep in sync."""
        state_items = (
            _state_item("a", "I've been based in Seattle.", rank=0),
        )
        matched = match_successor_predicate(
            parse_successor_predicate({"kind": "query_relevant"}),
            items=state_items,
            case=_Case(query="Seattle"),
        )
        self.assertEqual(matched, {"a"})

    def test_v1_never_receives_a_successor_kind(self) -> None:
        """v1's `_match_predicate` raises on an unhandled kind. If a v2
        connective delegated its whole subtree, v1 would see `slot_diverges`
        and raise. v2 must walk its own connectives."""
        predicate = parse_successor_predicate(
            {
                "kind": "and",
                "operands": [
                    {"kind": "slot_diverges"},
                    {"kind": "query_relevant"},
                ],
            }
        )
        matched = match_successor_predicate(
            predicate,
            items=(
                _state_item("m_stale", STALE_TEXT, rank=0),
                _state_item("m_current", CURRENT_TEXT, rank=1),
            ),
            case=_Case(query="where do I live Seattle Austin"),
        )
        self.assertEqual(matched, {"m_stale", "m_current"})

    def test_or_is_a_union_not_an_intersection(self) -> None:
        """Two leaves selecting disjoint sets. Under `and` the result is empty,
        under `or` it is both -- so this is the pair that tells the two
        connectives apart. Without it, `|=` and `&=` are interchangeable.
        """
        items = (
            _state_item("a", "I've been based in Seattle.", rank=0),
            _state_item("b", "Nothing relevant here.", rank=1, store="haystack"),
        )
        case = _Case(query="Seattle")
        relevant = match_successor_predicate(
            parse_successor_predicate({"kind": "query_relevant"}),
            items=items,
            case=case,
        )
        self.assertEqual(relevant, {"a"})

        # `age_gap_above` at 1.0 selects items at rank >= 1, i.e. exactly `b`.
        deep = match_successor_predicate(
            parse_successor_predicate(
                {"kind": "age_gap_above", "threshold": 1.0}
            ),
            items=items,
            case=case,
        )
        self.assertEqual(deep, {"b"})

        operands = [
            {"kind": "query_relevant"},
            {"kind": "age_gap_above", "threshold": 1.0},
        ]
        self.assertEqual(
            match_successor_predicate(
                parse_successor_predicate({"kind": "or", "operands": operands}),
                items=items,
                case=case,
            ),
            {"a", "b"},
        )
        self.assertEqual(
            match_successor_predicate(
                parse_successor_predicate({"kind": "and", "operands": operands}),
                items=items,
                case=case,
            ),
            set(),
        )

    def test_negation_is_over_the_present_items(self) -> None:
        """`not` is the complement within this state, matching v1's
        `all_ids - matched`. The haystack item makes the complement non-empty,
        which is what distinguishes "complement of the present items" from
        "complement of nothing" -- an all-selecting inner predicate returns the
        empty set either way.
        """
        predicate = parse_successor_predicate(
            {"kind": "not", "operands": [{"kind": "slot_diverges"}]}
        )
        matched = match_successor_predicate(
            predicate,
            items=(
                _state_item("m_stale", STALE_TEXT, rank=0),
                _state_item("m_current", CURRENT_TEXT, rank=1),
                _state_item("m_haystack", "Unrelated filler.", rank=2,
                            store="haystack"),
            ),
            case=_Case(),
        )
        self.assertEqual(matched, {"m_haystack"})


class NewLeafTest(unittest.TestCase):
    """The reason the successor exists at all."""

    def test_slot_diverges_fires_where_contradicts_does_not(self) -> None:
        items = (
            _state_item("m_stale", STALE_TEXT, rank=0),
            _state_item("m_current", CURRENT_TEXT, rank=1),
        )
        case = _Case()
        self.assertEqual(
            match_successor_predicate(
                parse_successor_predicate({"kind": "contradicts"}),
                items=items,
                case=case,
            ),
            set(),
        )
        self.assertEqual(
            match_successor_predicate(
                parse_successor_predicate({"kind": "slot_diverges"}),
                items=items,
                case=case,
            ),
            {"m_stale", "m_current"},
        )

    def test_untimestamped_filler_is_not_selected(self) -> None:
        matched = match_successor_predicate(
            parse_successor_predicate({"kind": "slot_diverges"}),
            items=(
                _state_item("m_stale", STALE_TEXT, rank=0),
                _state_item("m_current", CURRENT_TEXT, rank=1),
                _state_item("m_haystack", "Some unrelated filler.", rank=2,
                            store="haystack"),
            ),
            case=_Case(),
        )
        self.assertEqual(matched, {"m_stale", "m_current"})


class ParseTest(unittest.TestCase):
    def test_an_unregistered_kind_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            parse_successor_predicate({"kind": "reads_the_gold_answer"})

    def test_an_unregistered_key_is_refused(self) -> None:
        """§8.2: a case literal must have nowhere to be written."""
        with self.assertRaises(ValueError):
            parse_successor_predicate(
                {"kind": "slot_diverges", "memory_id": "m_stale"}
            )

    def test_a_v1_program_parses_unchanged_under_the_successor(self) -> None:
        """Migration has to be free, or the E0 winner cannot be compared."""
        v1_program = Sequence(
            body=(
                If(
                    predicate=Predicate(kind=PredicateKind.EVIDENCE_MISSING),
                    action=Action(kind=ActionKind.RETRIEVE_FILL),
                ),
            )
        )
        migrated = parse_successor_predicate(
            {"kind": "evidence_missing"},
        )
        self.assertEqual(migrated.kind.value, "evidence_missing")
        self.assertEqual(
            v1_program.body[0].predicate.kind.value,
            migrated.kind.value,
        )


if __name__ == "__main__":
    unittest.main()
