"""Route A E-1 tests: sealed state evaluator (BUILD SPEC §3.4, §3.6, §14.2)."""

import unittest

from cmd_audit.counterfactual.repair_state import (
    add_item,
    apply_disposition,
    initial_state_from_runtime_case,
    replace_item_text,
)
from cmd_audit.eval.state_fitness import evaluate_state
from cmd_audit.eval.state_intent import (
    HiddenStateIntent,
    PerturbationIntent,
    RequiredItemIntent,
    RuntimeEvent,
    RuntimeMemoryItem,
    RuntimeRepairCase,
)

RESOLVE_NODE = dict(operator_node_id="n0", predicate_id="p_stale")


def runtime_case(token_budget: int = 32) -> RuntimeRepairCase:
    """Stale-conflict shape: one current item, one superseded item."""
    return RuntimeRepairCase(
        case_id="c1",
        family_id="f1",
        query="Where does the user live now?",
        items=(
            RuntimeMemoryItem(
                item_id="i_current",
                text="The user lives in Lisbon.",
                source_event_ids=("e1",),
                store="episodic",
                rank=0,
            ),
            RuntimeMemoryItem(
                item_id="i_stale",
                text="The user lives in Porto.",
                source_event_ids=("e2",),
                store="episodic",
                rank=1,
            ),
        ),
        raw_events=(
            RuntimeEvent(event_id="e1", text="Moved to Lisbon in March."),
            RuntimeEvent(event_id="e2", text="Used to live in Porto."),
        ),
        token_budget=token_budget,
    )


def intent(token_budget: int = 32, **overrides) -> HiddenStateIntent:
    base = dict(
        case_id="c1",
        family_id="f1",
        required_items=(
            RequiredItemIntent(
                source_memory_id="i_current",
                required_phrases=("Lisbon",),
                allowed_dispositions=("active",),
            ),
        ),
        perturbations=(
            PerturbationIntent(
                target_item_id="i_stale",
                allowed_resolutions=("suppressed", "historical", "demoted"),
            ),
        ),
        protected_item_ids=("i_current",),
        allowed_added_item_ids=(),
        required_provenance_hashes=(),
        token_budget=token_budget,
        null_case=False,
    )
    base.update(overrides)
    return HiddenStateIntent(**base)


class PrimaryEndpointTest(unittest.TestCase):
    def test_resolving_the_perturbation_scores_success(self):
        state = apply_disposition(
            initial_state_from_runtime_case(runtime_case()),
            item_ids=("i_stale",),
            disposition="suppressed",
            **RESOLVE_NODE,
        )
        fitness = evaluate_state(state, intent())
        self.assertEqual(fitness.preserve_gold, 1.0)
        self.assertEqual(fitness.resolve_perturbation, 1.0)
        self.assertEqual(fitness.collateral_count, 0)
        self.assertEqual(fitness.state_success, 1)
        self.assertEqual(fitness.failure_reasons, ())

    def test_abstaining_scores_zero_on_an_unresolved_case(self):
        """§3.4: contract validity alone never makes abstention a repair."""
        state = initial_state_from_runtime_case(runtime_case())
        fitness = evaluate_state(state, intent())
        self.assertEqual(fitness.preserve_gold, 1.0)
        self.assertEqual(fitness.resolve_perturbation, 0.0)
        self.assertTrue(fitness.budget_valid)
        self.assertTrue(fitness.provenance_valid)
        self.assertEqual(fitness.state_success, 0)
        self.assertIn("perturbation_unresolved", fitness.failure_reasons)

    def test_an_already_present_replacement_does_not_resolve_anything(self):
        """A still-active perturbation is unresolved even if gold is present.

        Real intents name the gold item as the perturbation's replacement, and
        gold is active from the start. If a present replacement counted as
        resolution, every no-op would score success on every stale/conflict
        case — measured on the three dev datasets, exactly that happened.
        """
        state = initial_state_from_runtime_case(runtime_case())
        fitness = evaluate_state(
            state,
            intent(
                perturbations=(
                    PerturbationIntent(
                        target_item_id="i_stale",
                        allowed_resolutions=("suppressed", "historical", "demoted"),
                        replacement_item_ids=("i_current",),
                    ),
                )
            ),
        )
        self.assertEqual(fitness.resolve_perturbation, 0.0)
        self.assertEqual(fitness.state_success, 0)

    def test_disallowed_resolution_does_not_count(self):
        state = apply_disposition(
            initial_state_from_runtime_case(runtime_case()),
            item_ids=("i_stale",),
            disposition="conflict",
            **RESOLVE_NODE,
        )
        fitness = evaluate_state(
            state,
            intent(
                perturbations=(
                    PerturbationIntent(
                        target_item_id="i_stale",
                        allowed_resolutions=("suppressed",),
                    ),
                )
            ),
        )
        self.assertEqual(fitness.resolve_perturbation, 0.0)
        self.assertEqual(fitness.state_success, 0)

    def test_required_phrase_matches_case_insensitively(self):
        """Project convention is casefold matching (cmd_audit/scoring/phrase.py).

        The intent adapter admits a phrase on a casefold match, so an exact
        check here rejects items the adapter deemed valid — measured on memfail,
        70 hand-seed repairs failed preservation on nothing but capitalization.
        """
        state = apply_disposition(
            initial_state_from_runtime_case(runtime_case()),
            item_ids=("i_stale",),
            disposition="suppressed",
            **RESOLVE_NODE,
        )
        fitness = evaluate_state(
            state,
            intent(
                required_items=(
                    RequiredItemIntent(
                        source_memory_id="i_current",
                        required_phrases=("lives in lisbon",),
                        allowed_dispositions=("active",),
                    ),
                )
            ),
        )
        self.assertEqual(fitness.preserve_gold, 1.0)
        self.assertEqual(fitness.state_success, 1)

    def test_suppressing_the_gold_item_fails_preservation(self):
        state = apply_disposition(
            initial_state_from_runtime_case(runtime_case()),
            item_ids=("i_current", "i_stale"),
            disposition="suppressed",
            **RESOLVE_NODE,
        )
        fitness = evaluate_state(state, intent())
        self.assertEqual(fitness.preserve_gold, 0.0)
        self.assertEqual(fitness.state_success, 0)
        self.assertIn("gold_disposition:i_current", fitness.failure_reasons)


class CollateralTest(unittest.TestCase):
    def test_rewriting_a_protected_item_counts_as_collateral(self):
        state = replace_item_text(
            apply_disposition(
                initial_state_from_runtime_case(runtime_case()),
                item_ids=("i_stale",),
                disposition="suppressed",
                **RESOLVE_NODE,
            ),
            item_id="i_current",
            text="The user lives in Lisbon, Portugal, the capital city.",
            operator_node_id="n1",
            predicate_id="p_rewrite",
        )
        fitness = evaluate_state(state, intent(token_budget=64))
        self.assertEqual(fitness.collateral_count, 1)
        self.assertEqual(fitness.state_success, 0)
        self.assertIn("protected_rewritten:i_current", fitness.failure_reasons)

    def test_unauthorized_addition_counts_as_collateral(self):
        state = add_item(
            apply_disposition(
                initial_state_from_runtime_case(runtime_case()),
                item_ids=("i_stale",),
                disposition="suppressed",
                **RESOLVE_NODE,
            ),
            item_id="i_invented",
            text="The user probably lives in Lisbon.",
            operator_node_id="n1",
            predicate_id="p_add",
        )
        fitness = evaluate_state(state, intent(token_budget=64))
        self.assertEqual(fitness.collateral_count, 1)
        self.assertIn("unauthorized_addition:i_invented", fitness.failure_reasons)

    def test_context_stuffing_fails_collateral_or_budget(self):
        """§14.2: the same-budget stuffing baseline must not score success."""
        state = initial_state_from_runtime_case(runtime_case())
        for index in range(6):
            state = add_item(
                state,
                item_id=f"i_stuff_{index}",
                text="The user lives in Lisbon and also possibly Porto.",
                operator_node_id="n_stuff",
                predicate_id="p_stuff",
            )
        fitness = evaluate_state(state, intent())
        self.assertEqual(fitness.state_success, 0)
        self.assertTrue(
            fitness.collateral_count > 0 or not fitness.budget_valid,
            "stuffing must fail collateral or budget",
        )


class HardGateTest(unittest.TestCase):
    def test_budget_boundary_is_exact(self):
        state = apply_disposition(
            initial_state_from_runtime_case(runtime_case()),
            item_ids=("i_stale",),
            disposition="suppressed",
            **RESOLVE_NODE,
        )
        self.assertEqual(state.token_count, 5)
        self.assertTrue(evaluate_state(state, intent(token_budget=5)).budget_valid)
        at_limit = evaluate_state(state, intent(token_budget=5))
        self.assertEqual(at_limit.state_success, 1)
        under = evaluate_state(state, intent(token_budget=4))
        self.assertFalse(under.budget_valid)
        self.assertEqual(under.state_success, 0)

    def test_provenance_mutation_is_detected(self):
        base = initial_state_from_runtime_case(runtime_case())
        expected = (("i_current", base.items[0].provenance_hash),)
        mutated = replace_item_text(
            apply_disposition(
                base,
                item_ids=("i_stale",),
                disposition="suppressed",
                **RESOLVE_NODE,
            ),
            item_id="i_current",
            text="The user lives in Madrid.",
            operator_node_id="n1",
            predicate_id="p_rewrite",
        )
        fitness = evaluate_state(
            mutated, intent(required_provenance_hashes=expected)
        )
        self.assertFalse(fitness.provenance_valid)
        self.assertEqual(fitness.state_success, 0)
        self.assertIn("provenance_mismatch:i_current", fitness.failure_reasons)

    def test_untraced_state_difference_invalidates_the_trace(self):
        """§3.4: every state difference must be explained by a trace event."""
        state = apply_disposition(
            initial_state_from_runtime_case(runtime_case()),
            item_ids=("i_stale",),
            disposition="suppressed",
            **RESOLVE_NODE,
        )
        forged = type(state)(
            case_id=state.case_id,
            items=state.items,
            trace=(),
            rendered_context=state.rendered_context,
            token_count=state.token_count,
            state_hash=state.state_hash,
        )
        fitness = evaluate_state(forged, intent())
        self.assertFalse(fitness.trace_valid)
        self.assertEqual(fitness.state_success, 0)


class NullCaseTest(unittest.TestCase):
    def test_untouched_null_case_succeeds(self):
        state = initial_state_from_runtime_case(runtime_case())
        fitness = evaluate_state(
            state, intent(perturbations=(), null_case=True)
        )
        self.assertEqual(fitness.state_success, 1)

    def test_touching_a_null_case_fails(self):
        state = apply_disposition(
            initial_state_from_runtime_case(runtime_case()),
            item_ids=("i_current",),
            disposition="demoted",
            **RESOLVE_NODE,
        )
        fitness = evaluate_state(
            state, intent(perturbations=(), null_case=True)
        )
        self.assertEqual(fitness.state_success, 0)
        self.assertIn("null_case_modified", fitness.failure_reasons)


class DeterminismTest(unittest.TestCase):
    def test_same_state_and_intent_produce_identical_verdicts(self):
        def verdict():
            state = apply_disposition(
                initial_state_from_runtime_case(runtime_case()),
                item_ids=("i_stale",),
                disposition="suppressed",
                **RESOLVE_NODE,
            )
            return evaluate_state(state, intent())

        self.assertEqual(verdict(), verdict())


class SealedFeedbackTest(unittest.TestCase):
    def test_batch_feedback_exposes_only_aggregates(self):
        """§3.6: the proposer may not receive case IDs or per-case failures."""
        from cmd_audit.eval.state_fitness import summarize_batch_fitness

        states = []
        for case_index in range(2):
            state = initial_state_from_runtime_case(runtime_case())
            if case_index == 0:
                state = apply_disposition(
                    state,
                    item_ids=("i_stale",),
                    disposition="suppressed",
                    **RESOLVE_NODE,
                )
            states.append(state)
        vectors = tuple(evaluate_state(state, intent()) for state in states)
        feedback = summarize_batch_fitness(
            "batch_0", vectors, families=("f1", "f2")
        )
        self.assertEqual(feedback.cases, 2)
        self.assertEqual(feedback.families, 2)
        self.assertEqual(feedback.state_success_rate, 0.5)
        exposed = set(vars(feedback))
        self.assertNotIn("case_id", exposed)
        self.assertNotIn("failure_reasons", exposed)
        self.assertNotIn("cases_detail", exposed)


if __name__ == "__main__":
    unittest.main()
