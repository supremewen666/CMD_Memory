"""Legacy operator -> Route A closed-grammar conversion (BUILD SPEC §12.3).

§12.3 asks `operators.py` to "mark literal item hints legacy-only; prevent use
in Route A conversion". The *action* channel is already sealed: `translate_action`
raises on every action §6.1 excludes, so no item action can enter the typed IR by
being passed in.

The unsealed channel is the *parameter* one. `OperatorSpec` carries
`item_signal_hints` beside `steps`, so a spec whose steps are all in
`CLOSED_ACTIONS` translates cleanly while its hints are dropped on the floor.
The converted program is then not behaviorally equivalent to the legacy operator
it came from, and nothing says so. These tests pin the refusal, so the failure
mode is a raise rather than a silent behavioral change.
"""

import unittest
from unittest import mock

from cmd_audit.counterfactual import closed_grammar as closed_grammar_module
from cmd_audit.counterfactual.actions import PipelineAction
from cmd_audit.counterfactual.closed_grammar import (
    CLOSED_MAX_SEQUENCE_LENGTH,
    ClosedGrammarSpec,
    closed_grammar_manifest,
    closed_spec_from_operator,
)
from cmd_audit.counterfactual.operators import (
    LITERAL_ITEM_HINTS_PERMITTED_IN_ROUTE_A,
    LegacyOnlyChannelError,
    OperatorSpec,
    OperatorStep,
    assert_route_a_convertible,
)


class LegacyOnlyMarkingTest(unittest.TestCase):
    """The marking has to be inspectable, and one value, not two."""

    def test_literal_item_hints_are_not_permitted_in_route_a(self) -> None:
        self.assertFalse(LITERAL_ITEM_HINTS_PERMITTED_IN_ROUTE_A)

    def test_the_manifest_publishes_the_module_constant_not_a_copy(self) -> None:
        """§6.2's manifest field and the guard must not be able to disagree.

        Both values are `False` today, so comparing them cannot tell a read of
        the constant from a hardcoded literal. Flipping the constant is the only
        way to see which one the manifest reports -- and a hardcoded `False`
        would keep publishing "not permitted" after the guard was relaxed, the
        one direction that misleads a reader of the artifact.
        """
        with mock.patch.object(
            closed_grammar_module, "LITERAL_ITEM_HINTS_PERMITTED_IN_ROUTE_A", True
        ):
            self.assertTrue(closed_grammar_manifest()["literal_item_hints_permitted"])

    def test_the_error_is_catchable_as_a_value_error(self) -> None:
        """`closed_spec_from_operator`'s other refusals are plain `ValueError`s,
        so a caller that guards the whole conversion with one `except ValueError`
        must not let the hint refusal escape."""
        self.assertTrue(issubclass(LegacyOnlyChannelError, ValueError))


class GuardTest(unittest.TestCase):
    """`assert_route_a_convertible` on its own."""

    def test_a_hint_free_spec_passes(self) -> None:
        spec = OperatorSpec.from_actions(((0, PipelineAction.RETRIEVAL_ERROR),))
        assert_route_a_convertible(spec)

    def test_a_hint_carrying_spec_is_refused(self) -> None:
        spec = OperatorSpec.from_actions(
            ((0, PipelineAction.RETRIEVAL_ERROR),),
            item_signal_hints={"m3": -1.0},
        )
        with self.assertRaises(LegacyOnlyChannelError):
            assert_route_a_convertible(spec)

    def test_the_refusal_names_the_channel_and_the_reason(self) -> None:
        """The message is the only place a future caller learns why a spec that
        looks convertible is not."""
        spec = OperatorSpec().with_item_signal_hint("m3", -1.0)
        with self.assertRaises(LegacyOnlyChannelError) as caught:
            assert_route_a_convertible(spec)
        message = str(caught.exception)
        self.assertIn("item_signal_hints", message)
        self.assertIn("m3", message)

    def test_a_zero_weight_hint_still_counts_as_a_hint(self) -> None:
        """`0.0` is falsy but is still a recorded parameter, and dropping it
        changes the operator. A truthiness test on the weight would let it
        through."""
        spec = OperatorSpec(
            steps=(OperatorStep(0, PipelineAction.RETRIEVAL_ERROR),),
            item_signal_hints=(("m3", 0.0),),
        )
        with self.assertRaises(LegacyOnlyChannelError):
            assert_route_a_convertible(spec)


class ConversionTest(unittest.TestCase):
    """`closed_spec_from_operator`: the wired path."""

    def test_a_legal_single_action_spec_converts(self) -> None:
        spec = OperatorSpec.from_actions(((0, PipelineAction.INJECTION_ERROR),))
        converted = closed_spec_from_operator(spec)
        self.assertEqual(converted.actions, (PipelineAction.INJECTION_ERROR,))

    def test_conversion_refuses_a_hint_carrying_spec(self) -> None:
        """The guard is only worth having if the conversion calls it."""
        spec = OperatorSpec.from_actions(
            ((0, PipelineAction.RETRIEVAL_ERROR),),
            item_signal_hints={"m3": -1.0},
        )
        with self.assertRaises(LegacyOnlyChannelError):
            closed_spec_from_operator(spec)

    def test_an_ordered_multi_stage_spec_keeps_its_order(self) -> None:
        """§6.1 allows three actions at point 0, so the stage order is the
        content of the spec: reversing it is a different program.

        The pair is chosen so the causal order disagrees with the alphabetical
        one -- `"injection_error" < "retrieval_error"` -- because a conversion
        that sorted its stages would pass an `injection -> retrieval` assertion
        by coincidence.
        """
        spec = OperatorSpec(
            steps=(
                OperatorStep(0, PipelineAction.RETRIEVAL_ERROR),
                OperatorStep(0, PipelineAction.INJECTION_ERROR),
            )
        )
        converted = closed_spec_from_operator(spec)
        self.assertEqual(
            converted.actions,
            (PipelineAction.RETRIEVAL_ERROR, PipelineAction.INJECTION_ERROR),
        )

    def test_a_step_off_generation_point_zero_is_refused(self) -> None:
        """§6.1 fixes the generation point at 0. Converting a gp-1 step would
        silently relocate the repair to a point the sealed grammar never
        enumerated."""
        spec = OperatorSpec(steps=(OperatorStep(1, PipelineAction.RETRIEVAL_ERROR),))
        with self.assertRaises(ValueError) as caught:
            closed_spec_from_operator(spec)
        self.assertIn("generation point", str(caught.exception))

    def test_an_excluded_action_is_refused(self) -> None:
        """`SAFETY_ERROR` is outside the sealed grammar (§6.1)."""
        spec = OperatorSpec(steps=(OperatorStep(0, PipelineAction.SAFETY_ERROR),))
        with self.assertRaises(ValueError):
            closed_spec_from_operator(spec)

    def test_an_item_action_is_refused(self) -> None:
        spec = OperatorSpec(steps=(OperatorStep(0, PipelineAction.ITEM_STALE),))
        with self.assertRaises(ValueError):
            closed_spec_from_operator(spec)

    def test_an_over_length_spec_is_refused(self) -> None:
        spec = OperatorSpec(
            steps=tuple(
                OperatorStep(0, action)
                for action in (
                    PipelineAction.RETRIEVAL_ERROR,
                    PipelineAction.INJECTION_ERROR,
                    PipelineAction.GRANULARITY_ERROR,
                    PipelineAction.RETRIEVAL_ERROR,
                )
            )
        )
        self.assertGreater(len(spec.steps), CLOSED_MAX_SEQUENCE_LENGTH)
        with self.assertRaises(ValueError):
            closed_spec_from_operator(spec)

    def test_an_empty_spec_converts_to_the_identity_sequence(self) -> None:
        """The empty legacy spec is the registered null program, and E0's
        identity arm is the empty sequence, so the two must agree."""
        converted = closed_spec_from_operator(OperatorSpec())
        self.assertEqual(converted.actions, ())
        self.assertEqual(converted.format(), "identity")

    def test_conversion_agrees_with_the_enumerated_spec_of_the_same_shape(self) -> None:
        """A converted spec has to be the *same* program E0 scored, not merely a
        similar one, or a failure-memory seed would enter E3 under a hash the
        E0 matrix never used."""
        spec = OperatorSpec(
            steps=(
                OperatorStep(0, PipelineAction.INJECTION_ERROR),
                OperatorStep(0, PipelineAction.RETRIEVAL_ERROR),
            )
        )
        converted = closed_spec_from_operator(spec)
        enumerated = ClosedGrammarSpec(
            actions=(PipelineAction.INJECTION_ERROR, PipelineAction.RETRIEVAL_ERROR)
        )
        self.assertEqual(converted.content_hash(), enumerated.content_hash())
        self.assertEqual(
            converted.canonical_ast_hash(), enumerated.canonical_ast_hash()
        )


if __name__ == "__main__":
    unittest.main()
