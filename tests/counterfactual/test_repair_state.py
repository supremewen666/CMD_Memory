"""Route A E-1 tests: structured repair state (BUILD SPEC §3.2, §14.2)."""

import unittest

from cmd_audit.counterfactual.repair_state import (
    DISPOSITIONS,
    RepairStateError,
    apply_disposition,
    initial_state_from_runtime_case,
    render_state,
)
from cmd_audit.eval.state_intent import (
    RuntimeEvent,
    RuntimeMemoryItem,
    RuntimeRepairCase,
)


def runtime_case(token_budget: int = 512) -> RuntimeRepairCase:
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


def runtime_case_with_unretrieved_pool_item() -> RuntimeRepairCase:
    """The recorded run surfaced only the stale item; the current one stayed in
    the candidate pool. This is the real shape of a retrieval miss."""
    base = runtime_case()
    return RuntimeRepairCase(
        case_id=base.case_id,
        family_id=base.family_id,
        query=base.query,
        items=(
            RuntimeMemoryItem(
                item_id="i_current",
                text="The user lives in Lisbon.",
                source_event_ids=("e1",),
                store="episodic",
                rank=1,
                retrieved=False,
            ),
            RuntimeMemoryItem(
                item_id="i_stale",
                text="The user lives in Porto.",
                source_event_ids=("e2",),
                store="episodic",
                rank=0,
            ),
        ),
        raw_events=base.raw_events,
        token_budget=base.token_budget,
    )


class InitialStateTest(unittest.TestCase):
    def test_state_holds_only_what_the_pipeline_actually_recalled(self):
        """An unretrieved pool item is not part of the generator's state.

        It is a candidate an operator may pull in, not context the generator
        saw. Seeding it as an active state item makes a retrieval miss
        inexpressible and hands the no-op a free gold item.
        """
        state = initial_state_from_runtime_case(
            runtime_case_with_unretrieved_pool_item()
        )
        self.assertEqual(
            tuple(item.item_id for item in state.items), ("i_stale",)
        )
        self.assertNotIn("Lisbon", state.rendered_context)

    def test_all_items_start_active_in_runtime_rank_order(self):
        state = initial_state_from_runtime_case(runtime_case())
        self.assertEqual(
            tuple((item.item_id, item.rank, item.disposition) for item in state.items),
            (("i_current", 0, "active"), ("i_stale", 1, "active")),
        )
        self.assertEqual(state.trace, ())

    def test_state_hash_is_byte_identical_for_identical_input(self):
        self.assertEqual(
            initial_state_from_runtime_case(runtime_case()).state_hash,
            initial_state_from_runtime_case(runtime_case()).state_hash,
        )

    def test_provenance_hash_is_content_bound(self):
        """A rewritten item cannot keep its provenance hash (§3.4)."""
        state = initial_state_from_runtime_case(runtime_case())
        rewritten = RuntimeMemoryItem(
            item_id="i_current",
            text="The user lives in Madrid.",
            source_event_ids=("e1",),
            store="episodic",
            rank=0,
        )
        mutated = initial_state_from_runtime_case(
            RuntimeRepairCase(
                case_id="c1",
                family_id="f1",
                query=runtime_case().query,
                items=(rewritten,) + runtime_case().items[1:],
                raw_events=runtime_case().raw_events,
                token_budget=512,
            )
        )
        self.assertNotEqual(
            state.items[0].provenance_hash, mutated.items[0].provenance_hash
        )


class DispositionTransitionTest(unittest.TestCase):
    def test_transition_appends_trace_event_linking_before_and_after(self):
        before = initial_state_from_runtime_case(runtime_case())
        after = apply_disposition(
            before,
            item_ids=("i_stale",),
            disposition="suppressed",
            operator_node_id="n0",
            predicate_id="p_stale",
        )
        self.assertEqual(len(after.trace), 1)
        event = after.trace[0]
        self.assertEqual(event.before_hash, before.state_hash)
        self.assertEqual(event.after_hash, after.state_hash)
        self.assertEqual(event.matched_item_ids, ("i_stale",))
        self.assertEqual(event.action, "disposition:suppressed")
        self.assertEqual(before.trace, ())

    def test_suppressed_item_leaves_rendered_context(self):
        state = apply_disposition(
            initial_state_from_runtime_case(runtime_case()),
            item_ids=("i_stale",),
            disposition="suppressed",
            operator_node_id="n0",
            predicate_id="p_stale",
        )
        self.assertIn("Lisbon", state.rendered_context)
        self.assertNotIn("Porto", state.rendered_context)

    def test_unknown_disposition_is_rejected(self):
        with self.assertRaises(RepairStateError):
            apply_disposition(
                initial_state_from_runtime_case(runtime_case()),
                item_ids=("i_stale",),
                disposition="deleted_forever",
                operator_node_id="n0",
                predicate_id="p",
            )
        self.assertEqual(
            DISPOSITIONS,
            ("active", "demoted", "suppressed", "historical", "conflict"),
        )

    def test_rendering_cannot_change_fitness_state(self):
        """§3.2: rendering occurs after transition and is not a state change."""
        state = initial_state_from_runtime_case(runtime_case())
        self.assertEqual(render_state(state), state.rendered_context)
        self.assertEqual(
            initial_state_from_runtime_case(runtime_case()).state_hash,
            state.state_hash,
        )


if __name__ == "__main__":
    unittest.main()
