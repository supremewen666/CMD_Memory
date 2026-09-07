"""Exhaustive shallow-IR enumeration (BUILD SPEC §6.4, §14.3).

The load-bearing claim is exactness: "every canonical depth <= 2 IR program is
enumerated exactly once". These tests check that claim against an independently
computed count, not against the enumerator's own output.
"""

import unittest

from cmd_audit.counterfactual.behavior_fingerprint import behavior_fingerprint
from cmd_audit.counterfactual.program_ir import (
    AGE_GAP_THRESHOLDS,
    IDENTITY_ACTION_KINDS,
    REGISTERED_BOUNDS,
    SIMILARITY_THRESHOLDS,
    ActionKind,
    If,
    LEAF_PREDICATE_KINDS,
    ParameterizedPredicateKinds,
    PredicateKind,
    Sequence,
    canonical_ast_hash,
    canonicalize,
    check_resource_bounds,
    program_depth,
)
from cmd_audit.counterfactual.shallow_ir_enumerator import (
    SHALLOW_ENVELOPE_VERSION,
    SHALLOW_MAX_DEPTH,
    SHALLOW_SEQUENCE_LIMIT,
    ShallowEnvelope,
    count_shallow_ir_space,
    enumerate_shallow_programs,
    leaf_instantiations,
    shallow_envelope,
    shallow_grammar_manifest,
    shallow_predicates,
)

# Independently derived from the frozen grammar, not read back from the module.
EXPECTED_LEAVES = (len(LEAF_PREDICATE_KINDS) - len(ParameterizedPredicateKinds)) + (
    len(SIMILARITY_THRESHOLDS) + len(AGE_GAP_THRESHOLDS)
)
EXPECTED_ACTIONS = len(ActionKind) - len(IDENTITY_ACTION_KINDS)


class SpaceShapeTest(unittest.TestCase):
    """The registered envelope's dimensions come from the grammar, not a literal."""

    def test_leaf_instantiations_expand_the_threshold_grids(self) -> None:
        leaves = leaf_instantiations()
        self.assertEqual(len(leaves), EXPECTED_LEAVES)
        self.assertEqual(len(set(leaves)), len(leaves))

    def test_every_leaf_predicate_kind_appears(self) -> None:
        kinds = {predicate.kind for predicate in leaf_instantiations()}
        self.assertEqual(kinds, set(LEAF_PREDICATE_KINDS))

    def test_parameterized_leaves_appear_once_per_registered_threshold(self) -> None:
        leaves = leaf_instantiations()
        similarity = [
            leaf.threshold
            for leaf in leaves
            if leaf.kind is PredicateKind.SIMILARITY_ABOVE
        ]
        age_gap = [
            leaf.threshold for leaf in leaves if leaf.kind is PredicateKind.AGE_GAP_ABOVE
        ]
        self.assertEqual(sorted(similarity), sorted(SIMILARITY_THRESHOLDS))
        self.assertEqual(sorted(age_gap), sorted(AGE_GAP_THRESHOLDS))

    def test_shallow_predicates_are_leaves_not_and_or_over_leaves(self) -> None:
        """One level of predicate logic: Not(leaf), and And/Or over leaf subsets."""
        predicates = shallow_predicates()
        expected = (
            EXPECTED_LEAVES  # bare leaves
            + EXPECTED_LEAVES  # Not(leaf)
            + 2 * (2**EXPECTED_LEAVES - EXPECTED_LEAVES - 1)  # And/Or over subsets
        )
        self.assertEqual(len(predicates), expected)

    def test_shallow_predicates_are_pairwise_distinct(self) -> None:
        predicates = shallow_predicates()
        self.assertEqual(len(set(predicates)), len(predicates))


class ExactnessTest(unittest.TestCase):
    """§14.3 every canonical depth <= 2 program, exactly once."""

    def test_registered_depth_and_sequence_limit_are_frozen(self) -> None:
        self.assertEqual(SHALLOW_MAX_DEPTH, 2)
        self.assertLessEqual(SHALLOW_SEQUENCE_LIMIT, REGISTERED_BOUNDS.max_actions_per_case)

    def test_count_matches_an_independently_derived_formula(self) -> None:
        """Hand-derived: the null program, plus single-rule programs over every
        shallow predicate, plus ordered rule sequences with no adjacent repeat."""
        rules = EXPECTED_LEAVES * EXPECTED_ACTIONS
        single = len(shallow_predicates()) * EXPECTED_ACTIONS
        sequences = sum(
            rules * (rules - 1) ** (length - 1)
            for length in range(2, SHALLOW_SEQUENCE_LIMIT + 1)
        )
        self.assertEqual(count_shallow_ir_space(), 1 + single + sequences)

    def test_count_agrees_with_the_materialized_enumeration(self) -> None:
        """The analytic count is what the manifest freezes, so it must not drift
        from what the generator actually yields."""
        self.assertEqual(
            sum(1 for _ in enumerate_shallow_programs()), count_shallow_ir_space()
        )

    def test_every_program_is_yielded_exactly_once(self) -> None:
        hashes = [
            canonical_ast_hash(program) for program in enumerate_shallow_programs()
        ]
        self.assertEqual(len(set(hashes)), len(hashes))

    def test_every_program_is_already_canonical(self) -> None:
        """Enumerating a non-canonical form would double-count a behavior class."""
        offenders = [
            canonical_ast_hash(program)
            for program in enumerate_shallow_programs()
            if canonicalize(program) != program
        ]
        self.assertEqual(offenders, [])

    def test_every_program_is_within_the_registered_depth(self) -> None:
        too_deep = [
            program_depth(program)
            for program in enumerate_shallow_programs()
            if program_depth(program) > SHALLOW_MAX_DEPTH
        ]
        self.assertEqual(too_deep, [])

    def test_every_program_satisfies_the_registered_static_bounds(self) -> None:
        for program in enumerate_shallow_programs():
            check_resource_bounds(program)

    def test_the_null_program_is_in_the_space(self) -> None:
        """§9.1 `abstain-preserve` is a registered member of the envelope."""
        self.assertIn(Sequence(()), list(enumerate_shallow_programs()))

    def test_no_identity_action_program_is_enumerated(self) -> None:
        """A keep/preserve-only rule cannot change state, so it is not a member."""
        for program in enumerate_shallow_programs():
            if isinstance(program, If):
                with self.subTest(action=program.action.kind.value):
                    self.assertNotIn(program.action.kind, IDENTITY_ACTION_KINDS)

    def test_enumeration_order_is_deterministic(self) -> None:
        first = [canonical_ast_hash(p) for p in enumerate_shallow_programs(max_rules=1)]
        second = [canonical_ast_hash(p) for p in enumerate_shallow_programs(max_rules=1)]
        self.assertEqual(first, second)

    def test_a_smaller_sequence_limit_is_a_prefix_closed_subset(self) -> None:
        """Raising the limit adds programs; it never changes the shorter ones."""
        small = {canonical_ast_hash(p) for p in enumerate_shallow_programs(max_rules=1)}
        large = {canonical_ast_hash(p) for p in enumerate_shallow_programs(max_rules=2)}
        self.assertTrue(small < large)

    def test_the_registered_truncation_is_declared_lossy(self) -> None:
        """The truncation drops behavior classes that longer sequences reach, so
        the manifest must not read as if it were free."""
        manifest = shallow_grammar_manifest()
        self.assertTrue(manifest["truncation_is_lossy"])
        self.assertGreater(manifest["omitted_program_count"], 0)


class EnvelopeTest(unittest.TestCase):
    """§6.4 the envelope is the behaviorally deduplicated enumeration.

    Collapsing the full space costs a minute of fingerprinting, so these run on
    an explicit program list. The list is chosen to contain a known behavioral
    duplicate pair and a known distinct pair, which is what the collapse has to
    get right; exactness of the enumeration itself is covered above.
    """

    def sample(self) -> tuple:
        from cmd_audit.counterfactual.program_ir import Action, If, Predicate

        contradicts = Predicate(kind=PredicateKind.CONTRADICTS)
        relevant = Predicate(kind=PredicateKind.QUERY_RELEVANT)
        demote = If(predicate=contradicts, action=Action(ActionKind.DEMOTE))
        return (
            Sequence(()),
            demote,
            # Behaviorally identical to `demote`: the second rule cannot act.
            Sequence((demote, If(predicate=relevant, action=Action(ActionKind.KEEP)))),
            If(predicate=contradicts, action=Action(ActionKind.SUPPRESS)),
            If(predicate=relevant, action=Action(ActionKind.DEMOTE)),
        )

    def test_envelope_is_smaller_than_the_syntactic_space(self) -> None:
        """If dedup collapsed nothing, the fingerprint would not discriminate."""
        envelope = shallow_envelope(programs=self.sample())
        self.assertIsInstance(envelope, ShallowEnvelope)
        self.assertEqual(envelope.enumerated_count, 5)
        self.assertLess(envelope.behavior_class_count, envelope.enumerated_count)

    def test_envelope_members_have_pairwise_distinct_fingerprints(self) -> None:
        envelope = shallow_envelope(programs=self.sample())
        fingerprints = [member.behavior_fingerprint for member in envelope.members]
        self.assertEqual(len(set(fingerprints)), len(fingerprints))

    def test_envelope_records_the_representative_for_each_class(self) -> None:
        envelope = shallow_envelope(programs=self.sample())
        for member in envelope.members:
            with self.subTest(member=member.canonical_ast_hash):
                self.assertEqual(
                    behavior_fingerprint(member.program), member.behavior_fingerprint
                )

    def test_the_collapsed_member_records_the_hash_it_absorbed(self) -> None:
        """A collapse that forgot what it absorbed would make the envelope
        unauditable: a later candidate matching the padded form could not be
        traced to the class that already covered it."""
        envelope = shallow_envelope(programs=self.sample())
        absorbed = [m for m in envelope.members if m.collapsed_hashes]
        self.assertEqual(len(absorbed), 1)
        self.assertEqual(len(absorbed[0].collapsed_hashes), 1)

    def test_envelope_counts_collapsed_duplicates(self) -> None:
        envelope = shallow_envelope(programs=self.sample())
        self.assertEqual(
            envelope.enumerated_count - envelope.behavior_class_count,
            envelope.collapsed_count,
        )
        self.assertEqual(envelope.collapsed_count, 1)

    def test_envelope_is_deterministic(self) -> None:
        first = shallow_envelope(programs=self.sample())
        second = shallow_envelope(programs=self.sample())
        self.assertEqual(
            [m.canonical_ast_hash for m in first.members],
            [m.canonical_ast_hash for m in second.members],
        )
        self.assertEqual(first.envelope_sha256(), second.envelope_sha256())

    def test_envelope_digest_is_bound_to_the_probe_suite(self) -> None:
        """§6.4 freezes the envelope with its member fingerprints, which are only
        meaningful relative to the suite they were measured on."""
        envelope = shallow_envelope(programs=self.sample())
        from cmd_audit.counterfactual.behavior_fingerprint import probe_suite_sha256

        self.assertEqual(
            envelope.as_mapping()["probe_suite_sha256"], probe_suite_sha256()
        )


class ManifestTest(unittest.TestCase):
    """§6.4 the grammar manifest is frozen before E3."""

    def test_manifest_records_the_full_space_size_not_only_what_was_enumerated(
        self,
    ) -> None:
        """The enumerated envelope is a registered truncation of the literal
        depth <= 2 space, so the manifest must state both numbers or the
        exactness claim reads as covering more than it does."""
        manifest = shallow_grammar_manifest()
        self.assertEqual(manifest["shallow_envelope_version"], SHALLOW_ENVELOPE_VERSION)
        self.assertEqual(manifest["enumerated_program_count"], count_shallow_ir_space())
        self.assertGreater(
            manifest["depth2_space_size_at_registered_action_bound"],
            manifest["enumerated_program_count"],
        )
        self.assertIn("sequence_limit", manifest)
        self.assertIn("truncation_reason", manifest)

    def test_manifest_is_json_serializable_without_nan(self) -> None:
        import json

        json.dumps(shallow_grammar_manifest(), allow_nan=False, sort_keys=True)


if __name__ == "__main__":
    unittest.main()
