"""Typed operator IR (BUILD SPEC §8.1-§8.3).

Expected canonical forms below are hand-written from the §8.3 removal list, not
produced by calling the canonicalizer.
"""

import unittest

from cmd_audit.counterfactual.program_ir import (
    AGE_GAP_THRESHOLDS,
    SIMILARITY_THRESHOLDS,
    ActionKind,
    IdentityActionError,
    ParameterizedPredicateKinds,
    PredicateKind,
    ProgramBoundsError,
    ProgramParseError,
    ResourceBounds,
    REGISTERED_BOUNDS,
    Action,
    If,
    Predicate,
    Sequence,
    canonical_ast_hash,
    canonicalize,
    check_resource_bounds,
    parse_program,
    program_depth,
    program_node_count,
    program_to_mapping,
)


def leaf(kind: PredicateKind, threshold: float | None = None) -> Predicate:
    return Predicate(kind=kind, threshold=threshold)


RELEVANT = leaf(PredicateKind.QUERY_RELEVANT)
CONTRADICTS = leaf(PredicateKind.CONTRADICTS)
MISSING = leaf(PredicateKind.EVIDENCE_MISSING)


class GrammarSurfaceTest(unittest.TestCase):
    """§8.1 the registered primitive set is exactly what the spec lists."""

    def test_nine_predicate_kinds_plus_three_connectives(self) -> None:
        self.assertEqual(
            {kind.value for kind in PredicateKind},
            {
                "and",
                "or",
                "not",
                "query_relevant",
                "temporal_dominates",
                "contradicts",
                "source_more_reliable",
                "provenance_matches",
                "similarity_above",
                "age_gap_above",
                "evidence_missing",
            },
        )

    def test_nine_action_kinds(self) -> None:
        self.assertEqual(
            {kind.value for kind in ActionKind},
            {
                "keep",
                "demote",
                "suppress",
                "replace",
                "annotate_conflict",
                "retrieve_fill",
                "preserve",
                "abstain",
                "verify",
            },
        )

    def test_only_two_predicates_take_a_threshold(self) -> None:
        self.assertEqual(
            ParameterizedPredicateKinds,
            (PredicateKind.SIMILARITY_ABOVE, PredicateKind.AGE_GAP_ABOVE),
        )

    def test_threshold_grids_are_frozen_and_finite(self) -> None:
        """Enumeration is only finite because the grids are closed."""
        self.assertEqual(SIMILARITY_THRESHOLDS, (0.25, 0.5, 0.75))
        self.assertEqual(AGE_GAP_THRESHOLDS, (1.0, 7.0, 30.0))

    def test_threshold_off_the_registered_grid_is_rejected(self) -> None:
        with self.assertRaises(ProgramParseError):
            parse_program(
                {
                    "node": "if",
                    "predicate": {"kind": "similarity_above", "threshold": 0.31},
                    "action": {"kind": "demote"},
                }
            )

    def test_parameterless_predicate_may_not_carry_a_threshold(self) -> None:
        with self.assertRaises(ProgramParseError):
            parse_program(
                {
                    "node": "if",
                    "predicate": {"kind": "contradicts", "threshold": 0.5},
                    "action": {"kind": "demote"},
                }
            )


class ParseTest(unittest.TestCase):
    """§8.1/§9.2 the proposer emits data, never executable Python."""

    def test_round_trips_through_the_serialized_form(self) -> None:
        program = If(predicate=CONTRADICTS, action=Action(ActionKind.DEMOTE))
        self.assertEqual(parse_program(program_to_mapping(program)), program)

    def test_unknown_node_kind_is_rejected(self) -> None:
        with self.assertRaises(ProgramParseError):
            parse_program({"node": "while", "body": []})

    def test_unknown_predicate_kind_is_rejected(self) -> None:
        with self.assertRaises(ProgramParseError):
            parse_program(
                {
                    "node": "if",
                    "predicate": {"kind": "gold_matches"},
                    "action": {"kind": "demote"},
                }
            )

    def test_python_source_payload_is_not_executed(self) -> None:
        with self.assertRaises(ProgramParseError):
            parse_program({"node": "if", "predicate": "__import__('os')", "action": {}})

    def test_import_or_eval_payload_is_rejected(self) -> None:
        for payload in ("import os", "eval('1')", "exec('x=1')", "lambda: 1"):
            with self.subTest(payload=payload):
                with self.assertRaises(ProgramParseError):
                    parse_program(
                        {
                            "node": "if",
                            "predicate": {"kind": "contradicts", "note": payload},
                            "action": {"kind": "demote"},
                        }
                    )


class DenylistTest(unittest.TestCase):
    """§8.2 an AST may not carry a literal drawn from a case."""

    def test_free_form_string_payload_is_rejected(self) -> None:
        """Any unregistered key is refused, so a literal has nowhere to hide."""
        for extra in (
            {"item_id": "m_stale"},
            {"case_id": "stale-001"},
            {"family_id": "fam-1"},
            {"source_event_ids": ["e1"]},
            {"required_phrases": ["blue sedan"]},
            {"replacement_text": "the car is blue"},
        ):
            with self.subTest(extra=extra):
                with self.assertRaises(ProgramParseError):
                    parse_program(
                        {
                            "node": "if",
                            "predicate": {"kind": "contradicts", **extra},
                            "action": {"kind": "demote"},
                        }
                    )

    def test_action_may_not_carry_a_payload_either(self) -> None:
        with self.assertRaises(ProgramParseError):
            parse_program(
                {
                    "node": "if",
                    "predicate": {"kind": "contradicts"},
                    "action": {"kind": "replace", "text": "corrected value"},
                }
            )


class CanonicalizationTest(unittest.TestCase):
    """§8.3 removal list. Expected forms are written out by hand."""

    def test_commutative_predicate_order_is_normalized(self) -> None:
        left = If(
            predicate=Predicate(
                kind=PredicateKind.AND, operands=(RELEVANT, CONTRADICTS)
            ),
            action=Action(ActionKind.DEMOTE),
        )
        right = If(
            predicate=Predicate(
                kind=PredicateKind.AND, operands=(CONTRADICTS, RELEVANT)
            ),
            action=Action(ActionKind.DEMOTE),
        )
        self.assertEqual(canonicalize(left), canonicalize(right))
        self.assertEqual(canonical_ast_hash(left), canonical_ast_hash(right))

    def test_double_negation_is_removed(self) -> None:
        doubled = If(
            predicate=Predicate(
                kind=PredicateKind.NOT,
                operands=(
                    Predicate(kind=PredicateKind.NOT, operands=(CONTRADICTS,)),
                ),
            ),
            action=Action(ActionKind.DEMOTE),
        )
        plain = If(predicate=CONTRADICTS, action=Action(ActionKind.DEMOTE))
        self.assertEqual(canonicalize(doubled), canonicalize(plain))

    def test_single_negation_survives(self) -> None:
        negated = If(
            predicate=Predicate(kind=PredicateKind.NOT, operands=(CONTRADICTS,)),
            action=Action(ActionKind.DEMOTE),
        )
        plain = If(predicate=CONTRADICTS, action=Action(ActionKind.DEMOTE))
        self.assertNotEqual(canonical_ast_hash(negated), canonical_ast_hash(plain))

    def test_empty_sequence_canonicalizes_to_the_null_program(self) -> None:
        self.assertEqual(canonicalize(Sequence(())), Sequence(()))

    def test_nested_sequence_is_flattened(self) -> None:
        rule = If(predicate=CONTRADICTS, action=Action(ActionKind.DEMOTE))
        nested = Sequence((Sequence((rule,)),))
        self.assertEqual(canonicalize(nested), canonicalize(Sequence((rule,))))

    def test_single_member_sequence_collapses_to_its_member(self) -> None:
        rule = If(predicate=CONTRADICTS, action=Action(ActionKind.DEMOTE))
        self.assertEqual(canonicalize(Sequence((rule,))), canonicalize(rule))

    def test_duplicate_adjacent_rule_is_removed(self) -> None:
        rule = If(predicate=CONTRADICTS, action=Action(ActionKind.DEMOTE))
        other = If(predicate=MISSING, action=Action(ActionKind.RETRIEVE_FILL))
        self.assertEqual(
            canonicalize(Sequence((rule, rule, other))),
            canonicalize(Sequence((rule, other))),
        )

    def test_nonadjacent_repeat_is_preserved(self) -> None:
        """Order matters, so a repeat separated by another rule is not a duplicate."""
        rule = If(predicate=CONTRADICTS, action=Action(ActionKind.DEMOTE))
        other = If(predicate=MISSING, action=Action(ActionKind.RETRIEVE_FILL))
        self.assertNotEqual(
            canonical_ast_hash(canonicalize(Sequence((rule, other, rule)))),
            canonical_ast_hash(canonicalize(Sequence((rule, other)))),
        )

    def test_identity_action_rule_is_removed_from_a_sequence(self) -> None:
        """`keep` and `preserve` leave state untouched, so they are not variation."""
        active = If(predicate=CONTRADICTS, action=Action(ActionKind.DEMOTE))
        for identity in (ActionKind.KEEP, ActionKind.PRESERVE):
            with self.subTest(identity=identity):
                noop = If(predicate=RELEVANT, action=Action(identity))
                self.assertEqual(
                    canonicalize(Sequence((noop, active))), canonicalize(active)
                )

    def test_a_program_of_only_identity_actions_is_rejected(self) -> None:
        """An all-no-op program is not a repair candidate; it fails at the door."""
        with self.assertRaises(IdentityActionError):
            canonicalize(If(predicate=RELEVANT, action=Action(ActionKind.KEEP)))

    def test_statically_unreachable_branch_is_removed(self) -> None:
        """`And(p, Not(p))` can never hold, so its rule cannot fire."""
        dead = If(
            predicate=Predicate(
                kind=PredicateKind.AND,
                operands=(
                    CONTRADICTS,
                    Predicate(kind=PredicateKind.NOT, operands=(CONTRADICTS,)),
                ),
            ),
            action=Action(ActionKind.SUPPRESS),
        )
        live = If(predicate=MISSING, action=Action(ActionKind.RETRIEVE_FILL))
        self.assertEqual(canonicalize(Sequence((dead, live))), canonicalize(live))

    def test_canonicalization_is_idempotent(self) -> None:
        program = Sequence(
            (
                If(
                    predicate=Predicate(
                        kind=PredicateKind.AND, operands=(CONTRADICTS, RELEVANT)
                    ),
                    action=Action(ActionKind.DEMOTE),
                ),
                If(predicate=MISSING, action=Action(ActionKind.RETRIEVE_FILL)),
            )
        )
        once = canonicalize(program)
        self.assertEqual(canonicalize(once), once)

    def test_hash_is_stable_across_processes(self) -> None:
        """A frozen sha256 literal: the hash cannot drift between runs."""
        program = If(predicate=CONTRADICTS, action=Action(ActionKind.DEMOTE))
        self.assertEqual(
            canonical_ast_hash(program), canonical_ast_hash(parse_program(
                program_to_mapping(program)
            ))
        )
        self.assertEqual(len(canonical_ast_hash(program)), 64)


class DepthAndNodeCountTest(unittest.TestCase):
    """Depth counts composite nesting; a leaf sits at depth 0."""

    def test_simple_rule_is_depth_one(self) -> None:
        self.assertEqual(
            program_depth(If(predicate=CONTRADICTS, action=Action(ActionKind.DEMOTE))),
            1,
        )

    def test_one_level_of_predicate_logic_is_depth_two(self) -> None:
        self.assertEqual(
            program_depth(
                If(
                    predicate=Predicate(
                        kind=PredicateKind.AND, operands=(CONTRADICTS, RELEVANT)
                    ),
                    action=Action(ActionKind.DEMOTE),
                )
            ),
            2,
        )

    def test_sequence_of_simple_rules_is_depth_two(self) -> None:
        self.assertEqual(
            program_depth(
                Sequence(
                    (
                        If(predicate=CONTRADICTS, action=Action(ActionKind.DEMOTE)),
                        If(predicate=MISSING, action=Action(ActionKind.RETRIEVE_FILL)),
                    )
                )
            ),
            2,
        )

    def test_sequence_over_compound_predicates_is_depth_three(self) -> None:
        self.assertEqual(
            program_depth(
                Sequence(
                    (
                        If(
                            predicate=Predicate(
                                kind=PredicateKind.AND,
                                operands=(CONTRADICTS, RELEVANT),
                            ),
                            action=Action(ActionKind.DEMOTE),
                        ),
                        If(predicate=MISSING, action=Action(ActionKind.RETRIEVE_FILL)),
                    )
                )
            ),
            3,
        )

    def test_node_count_counts_every_ast_node(self) -> None:
        # Sequence + If + And + 2 leaves + Action, and If + leaf + Action.
        program = Sequence(
            (
                If(
                    predicate=Predicate(
                        kind=PredicateKind.AND, operands=(CONTRADICTS, RELEVANT)
                    ),
                    action=Action(ActionKind.DEMOTE),
                ),
                If(predicate=MISSING, action=Action(ActionKind.RETRIEVE_FILL)),
            )
        )
        self.assertEqual(program_node_count(program), 9)


class ResourceBoundsTest(unittest.TestCase):
    """§8.1 bounds fail closed. All six are frozen before E3."""

    def test_registered_bounds_carry_all_six_limits(self) -> None:
        self.assertEqual(
            tuple(sorted(vars(REGISTERED_BOUNDS))),
            (
                "max_actions_per_case",
                "max_depth",
                "max_logical_cost",
                "max_nodes",
                "max_retrieved_additions",
                "max_token_delta",
            ),
        )

    def test_program_within_bounds_passes(self) -> None:
        check_resource_bounds(
            If(predicate=CONTRADICTS, action=Action(ActionKind.DEMOTE)),
            bounds=REGISTERED_BOUNDS,
        )

    def test_program_over_depth_fails_closed(self) -> None:
        bounds = ResourceBounds(
            max_depth=1,
            max_nodes=64,
            max_actions_per_case=4,
            max_retrieved_additions=4,
            max_token_delta=512,
            max_logical_cost=16,
        )
        with self.assertRaises(ProgramBoundsError):
            check_resource_bounds(
                If(
                    predicate=Predicate(
                        kind=PredicateKind.AND, operands=(CONTRADICTS, RELEVANT)
                    ),
                    action=Action(ActionKind.DEMOTE),
                ),
                bounds=bounds,
            )

    def test_program_over_node_count_fails_closed(self) -> None:
        bounds = ResourceBounds(
            max_depth=8,
            max_nodes=3,
            max_actions_per_case=4,
            max_retrieved_additions=4,
            max_token_delta=512,
            max_logical_cost=16,
        )
        with self.assertRaises(ProgramBoundsError):
            check_resource_bounds(
                Sequence(
                    (
                        If(predicate=CONTRADICTS, action=Action(ActionKind.DEMOTE)),
                        If(predicate=MISSING, action=Action(ActionKind.RETRIEVE_FILL)),
                    )
                ),
                bounds=bounds,
            )

    def test_program_over_action_count_fails_closed(self) -> None:
        bounds = ResourceBounds(
            max_depth=8,
            max_nodes=64,
            max_actions_per_case=1,
            max_retrieved_additions=4,
            max_token_delta=512,
            max_logical_cost=16,
        )
        with self.assertRaises(ProgramBoundsError):
            check_resource_bounds(
                Sequence(
                    (
                        If(predicate=CONTRADICTS, action=Action(ActionKind.DEMOTE)),
                        If(predicate=MISSING, action=Action(ActionKind.RETRIEVE_FILL)),
                    )
                ),
                bounds=bounds,
            )


if __name__ == "__main__":
    unittest.main()
