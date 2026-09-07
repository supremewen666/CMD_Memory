"""Sealed legacy grammar enumeration (BUILD SPEC §6.1, §6.2, §14.3).

The load-bearing claims are that the enumeration is finite and deterministic,
that content-hash deduplication is exact, and that the safety and literal-hint
channels are excluded. §14.3 names all three.

The state-surface translation gets its own class: it is the one place E0 can
silently stop measuring the operator it claims to reproduce, and a mapping that
compiles is not a mapping that behaves.
"""

import unittest

from cmd_audit.counterfactual.actions import (
    PIPELINE_ACTION_OPERATOR_DSL,
    PipelineAction,
)
from cmd_audit.counterfactual.behavior_fingerprint import behavior_fingerprint
from cmd_audit.counterfactual.closed_grammar import (
    CLOSED_ACTIONS,
    CLOSED_GRAMMAR_VERSION,
    CLOSED_MAX_SEQUENCE_LENGTH,
    EXCLUDED_ACTIONS,
    ClosedGrammarSpec,
    canonical_closed_specs,
    closed_grammar_manifest,
    count_canonical_closed_grammar,
    count_closed_grammar,
    enumerate_closed_specs,
    translate_action,
)
from cmd_audit.counterfactual.program_ir import (
    REGISTERED_BOUNDS,
    ActionKind,
    If,
    PredicateKind,
    Sequence,
    canonicalize,
    check_resource_bounds,
    program_depth,
)


class SealedGrammarTest(unittest.TestCase):
    """§6.1 the grammar is closed, and closed at the registered shape."""

    def test_action_set_is_the_three_non_safety_non_item_actions(self) -> None:
        self.assertEqual(len(CLOSED_ACTIONS), 3)
        self.assertEqual(
            set(CLOSED_ACTIONS),
            {
                PipelineAction.RETRIEVAL_ERROR,
                PipelineAction.INJECTION_ERROR,
                PipelineAction.GRANULARITY_ERROR,
            },
        )

    def test_safety_error_is_excluded_with_a_recorded_reason(self) -> None:
        """§6.1 excludes it because its eligibility path is label-equivalent."""
        excluded = dict(EXCLUDED_ACTIONS)
        self.assertIn(PipelineAction.SAFETY_ERROR, excluded)
        self.assertNotIn(PipelineAction.SAFETY_ERROR, CLOSED_ACTIONS)

    def test_every_item_action_is_excluded(self) -> None:
        """Item actions are keyed by literal memory IDs, forbidden by §6.1."""
        excluded = {action for action, _ in EXCLUDED_ACTIONS}
        item_actions = {
            action
            for action in PipelineAction
            if action.value.startswith("item_")
        }
        self.assertTrue(item_actions)
        self.assertTrue(item_actions <= excluded)
        self.assertFalse(item_actions & set(CLOSED_ACTIONS))

    def test_the_excluded_set_plus_the_closed_set_covers_every_real_action(
        self,
    ) -> None:
        """A new legacy action must be classified, not silently ignored."""
        classified = set(CLOSED_ACTIONS) | {a for a, _ in EXCLUDED_ACTIONS}
        real = {a for a in PipelineAction if a is not PipelineAction.IDENTITY}
        self.assertEqual(classified, real)

    def test_an_excluded_action_cannot_be_translated(self) -> None:
        with self.assertRaises(ValueError):
            translate_action(PipelineAction.SAFETY_ERROR)
        with self.assertRaises(ValueError):
            translate_action(PipelineAction.ITEM_POISONED)

    def test_an_excluded_action_cannot_enter_a_spec(self) -> None:
        with self.assertRaises(ValueError):
            ClosedGrammarSpec(actions=(PipelineAction.SAFETY_ERROR,))

    def test_sequence_length_is_bounded_at_three(self) -> None:
        self.assertEqual(CLOSED_MAX_SEQUENCE_LENGTH, 3)
        with self.assertRaises(ValueError):
            ClosedGrammarSpec(
                actions=tuple([PipelineAction.RETRIEVAL_ERROR] * 4)
            )


class EnumerationTest(unittest.TestCase):
    """§14.3 finite legacy enumeration has a deterministic count."""

    def test_raw_count_matches_an_independently_derived_formula(self) -> None:
        """Hand-derived: every ordered sequence of length 0..3 over 3 actions."""
        expected = sum(3**length for length in range(CLOSED_MAX_SEQUENCE_LENGTH + 1))
        self.assertEqual(count_closed_grammar(), expected)
        self.assertEqual(expected, 40)

    def test_raw_count_agrees_with_the_materialized_enumeration(self) -> None:
        self.assertEqual(sum(1 for _ in enumerate_closed_specs()), count_closed_grammar())

    def test_canonical_count_matches_an_independently_derived_formula(self) -> None:
        """No-adjacent-repeat sequences: 1 + 3 + 3*2 + 3*2*2."""
        expected = 1 + 3 + 3 * 2 + 3 * 2 * 2
        self.assertEqual(count_canonical_closed_grammar(), expected)
        self.assertEqual(expected, 22)

    def test_canonical_count_agrees_with_content_hash_deduplication(self) -> None:
        """§14.3 content-hash deduplication is exact."""
        self.assertEqual(
            len(canonical_closed_specs()), count_canonical_closed_grammar()
        )

    def test_deduplication_actually_collapses_something(self) -> None:
        """Raw > canonical, or the dedup step is keyed on the wrong thing.

        Keying `content_hash` on the raw stage list would make every generated
        sequence unique and both §6.2 counts identical, which would read as
        "nothing was duplicated" rather than "dedup did nothing".
        """
        self.assertGreater(count_closed_grammar(), len(canonical_closed_specs()))

    def test_adjacent_repeats_share_a_content_hash(self) -> None:
        once = ClosedGrammarSpec(actions=(PipelineAction.RETRIEVAL_ERROR,))
        twice = ClosedGrammarSpec(
            actions=(PipelineAction.RETRIEVAL_ERROR, PipelineAction.RETRIEVAL_ERROR)
        )
        self.assertEqual(once.content_hash(), twice.content_hash())

    def test_a_separated_repeat_is_a_distinct_sequence(self) -> None:
        """The second pass observes a state the first did not, so it is kept."""
        separated = ClosedGrammarSpec(
            actions=(
                PipelineAction.RETRIEVAL_ERROR,
                PipelineAction.INJECTION_ERROR,
                PipelineAction.RETRIEVAL_ERROR,
            )
        )
        single = ClosedGrammarSpec(actions=(PipelineAction.RETRIEVAL_ERROR,))
        self.assertNotEqual(separated.content_hash(), single.content_hash())
        self.assertEqual(len(separated.canonical_actions), 3)

    def test_content_hashes_are_pairwise_distinct(self) -> None:
        hashes = [spec.content_hash() for spec in canonical_closed_specs()]
        self.assertEqual(len(set(hashes)), len(hashes))

    def test_enumeration_order_is_deterministic(self) -> None:
        first = [s.content_hash() for s in enumerate_closed_specs()]
        second = [s.content_hash() for s in enumerate_closed_specs()]
        self.assertEqual(first, second)

    def test_the_null_program_is_the_length_zero_sequence(self) -> None:
        """§9.1 `abstain-preserve` is a member, and it is the empty sequence."""
        first = next(iter(enumerate_closed_specs()))
        self.assertEqual(first.actions, ())
        self.assertEqual(first.program, Sequence(()))

    def test_every_spec_is_within_the_registered_bounds(self) -> None:
        for spec in canonical_closed_specs():
            with self.subTest(spec=spec.format()):
                check_resource_bounds(
                    spec.canonical_program, bounds=REGISTERED_BOUNDS
                )

    def test_every_spec_program_is_already_canonical(self) -> None:
        for spec in canonical_closed_specs():
            with self.subTest(spec=spec.format()):
                self.assertEqual(canonicalize(spec.program), spec.program)


class StateTranslationTest(unittest.TestCase):
    """§12.1/§12.3 the legacy actions execute as state transitions.

    Legacy `apply_pipeline_action` appends text to a rendered context string;
    `state_success` is measured on `RepairState`. These tests pin the mapping so
    a silent change to it shows up here rather than as a shifted headroom number.
    """

    def test_every_closed_action_has_a_frozen_dsl_entry(self) -> None:
        """The translation must be anchored to §6.1's frozen mapping."""
        for action in CLOSED_ACTIONS:
            with self.subTest(action=action.value):
                self.assertIn(action, PIPELINE_ACTION_OPERATOR_DSL)

    def test_retrieval_error_is_the_only_action_that_reads_the_pool(self) -> None:
        """MISSED_CANDIDATES x ADD_FROM_STORE: only `retrieve_fill` adds items."""
        rule = translate_action(PipelineAction.RETRIEVAL_ERROR)
        self.assertEqual(rule.action.kind, ActionKind.RETRIEVE_FILL)
        for other in (PipelineAction.INJECTION_ERROR, PipelineAction.GRANULARITY_ERROR):
            with self.subTest(action=other.value):
                self.assertNotEqual(
                    translate_action(other).action.kind, ActionKind.RETRIEVE_FILL
                )

    def test_injection_error_reorders_without_withholding_text(self) -> None:
        """RE_EMIT_ORDERED changes precedence, so it demotes rather than suppresses."""
        rule = translate_action(PipelineAction.INJECTION_ERROR)
        self.assertEqual(rule.action.kind, ActionKind.DEMOTE)
        self.assertEqual(rule.predicate.kind, PredicateKind.TEMPORAL_DOMINATES)

    def test_granularity_error_acts_on_recall_not_on_the_pool(self) -> None:
        """A granularity failure is a recalled coarse item, so no pool read."""
        rule = translate_action(PipelineAction.GRANULARITY_ERROR)
        self.assertNotEqual(rule.action.kind, ActionKind.RETRIEVE_FILL)
        self.assertEqual(rule.action.kind, ActionKind.REPLACE)

    def test_the_three_actions_are_behaviorally_distinct(self) -> None:
        """A translation that collapsed two actions would understate the grammar.

        §8.3's definition of "not a variation" is a shared fingerprint, so if two
        legacy actions mapped onto the same behavior the closed envelope would be
        smaller than the operators the live system actually shipped.
        """
        fingerprints = {
            action: behavior_fingerprint(translate_action(action))
            for action in CLOSED_ACTIONS
        }
        self.assertEqual(len(set(fingerprints.values())), len(CLOSED_ACTIONS))

    def test_no_translated_rule_exceeds_the_shallow_depth(self) -> None:
        """Each single action stays within depth 2, so sequences stay within 3."""
        for action in CLOSED_ACTIONS:
            with self.subTest(action=action.value):
                self.assertLessEqual(program_depth(translate_action(action)), 2)

    def test_translated_rules_carry_no_literal_item_hint(self) -> None:
        """§6.1 forbids literal item hints; the typed IR has no channel for one."""
        for action in CLOSED_ACTIONS:
            rule = translate_action(action)
            with self.subTest(action=action.value):
                self.assertIsInstance(rule, If)
                self.assertFalse(hasattr(rule, "item_signal_hints"))

    def test_a_sequence_executes_its_stages_in_order(self) -> None:
        spec = ClosedGrammarSpec(
            actions=(PipelineAction.RETRIEVAL_ERROR, PipelineAction.INJECTION_ERROR)
        )
        program = spec.program
        self.assertIsInstance(program, Sequence)
        self.assertEqual(
            [rule.action.kind for rule in program.body],
            [ActionKind.RETRIEVE_FILL, ActionKind.DEMOTE],
        )

    def test_order_is_preserved_rather_than_sorted(self) -> None:
        """Reversing the stages must produce a different program, or §6.1's
        sequence dimension is not being enumerated at all."""
        forward = ClosedGrammarSpec(
            actions=(PipelineAction.RETRIEVAL_ERROR, PipelineAction.INJECTION_ERROR)
        )
        backward = ClosedGrammarSpec(
            actions=(PipelineAction.INJECTION_ERROR, PipelineAction.RETRIEVAL_ERROR)
        )
        self.assertNotEqual(forward.content_hash(), backward.content_hash())
        self.assertNotEqual(forward.canonical_ast_hash(), backward.canonical_ast_hash())


class ManifestTest(unittest.TestCase):
    """§6.2 `closed_grammar_manifest.json`."""

    def test_manifest_reports_both_counts(self) -> None:
        manifest = closed_grammar_manifest()
        self.assertEqual(manifest["closed_grammar_version"], CLOSED_GRAMMAR_VERSION)
        self.assertEqual(manifest["raw_generated_count"], count_closed_grammar())
        self.assertEqual(
            manifest["canonical_unique_count"], count_canonical_closed_grammar()
        )
        self.assertEqual(manifest["generation_point"], 0)

    def test_manifest_declares_literal_hints_forbidden(self) -> None:
        self.assertFalse(closed_grammar_manifest()["literal_item_hints_permitted"])

    def test_manifest_publishes_the_exclusion_reasons(self) -> None:
        """A reader must see why the action space is 3 and not 9."""
        excluded = closed_grammar_manifest()["excluded_actions"]
        self.assertEqual(len(excluded), len(EXCLUDED_ACTIONS))
        for entry in excluded:
            with self.subTest(action=entry["action"]):
                self.assertTrue(entry["reason"])

    def test_manifest_publishes_the_state_translation_for_each_action(self) -> None:
        """The rewrite from string surface to state surface is the one step a
        reader cannot reconstruct from the spec, so it must be in the artifact."""
        translation = closed_grammar_manifest()["action_translation"]
        self.assertEqual(set(translation), {a.value for a in CLOSED_ACTIONS})
        for action, entry in translation.items():
            with self.subTest(action=action):
                self.assertTrue(entry["selector"])
                self.assertTrue(entry["transform"])
                self.assertTrue(entry["rationale"])
                self.assertIn("node", entry["ir_predicate"])

    def test_manifest_is_json_serializable_without_nan(self) -> None:
        import json

        json.dumps(closed_grammar_manifest(), allow_nan=False, sort_keys=True)


if __name__ == "__main__":
    unittest.main()
