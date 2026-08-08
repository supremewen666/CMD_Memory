"""IR execution against structured repair state (BUILD SPEC §3.2, §8.1).

Seams under test: `execute_program` (typed IR -> RepairState) and
`execute_closed_spec` (legacy closed grammar -> RepairState). Both are observed
only through the returned state and its trace, never through internals.
"""

import unittest

from cmd_audit.counterfactual.program_ir import (
    Action,
    ActionKind,
    If,
    Predicate,
    PredicateKind,
    ResourceBounds,
    Sequence,
)
from cmd_audit.counterfactual.repair_state import (
    initial_state_from_runtime_case,
)
from cmd_audit.counterfactual.state_executor import (
    ExecutionLimitError,
    NULL_PROGRAM,
    execute_program,
)
from cmd_audit.eval.state_intent import (
    RuntimeEvent,
    RuntimeMemoryItem,
    RuntimeRepairCase,
)

LOOSE_BOUNDS = ResourceBounds(
    max_depth=8,
    max_nodes=64,
    max_actions_per_case=16,
    max_retrieved_additions=8,
    max_token_delta=4096,
    max_logical_cost=64,
)


def case(
    *,
    query: str = "where does dana work now",
    items: tuple[RuntimeMemoryItem, ...],
    events: tuple[RuntimeEvent, ...] = (),
    token_budget: int = 500,
) -> RuntimeRepairCase:
    return RuntimeRepairCase(
        case_id="c1",
        family_id="f1",
        query=query,
        items=items,
        raw_events=events,
        token_budget=token_budget,
    )


def item(
    item_id: str,
    text: str,
    *,
    rank: int = 0,
    retrieved: bool = True,
    store: str = "default",
    events: tuple[str, ...] = (),
) -> RuntimeMemoryItem:
    return RuntimeMemoryItem(
        item_id=item_id,
        text=text,
        source_event_ids=events,
        store=store,
        rank=rank,
        retrieved=retrieved,
    )


CONTRADICTS = Predicate(kind=PredicateKind.CONTRADICTS)
RELEVANT = Predicate(kind=PredicateKind.QUERY_RELEVANT)
MISSING = Predicate(kind=PredicateKind.EVIDENCE_MISSING)


class NullProgramTest(unittest.TestCase):
    def test_null_program_leaves_state_byte_identical(self) -> None:
        runtime = case(items=(item("a", "dana works at acme"),))
        start = initial_state_from_runtime_case(runtime)
        result = execute_program(NULL_PROGRAM, runtime, start, bounds=LOOSE_BOUNDS)
        self.assertEqual(result.state.state_hash, start.state_hash)
        self.assertEqual(result.state.trace, ())


class PredicateMatchingTest(unittest.TestCase):
    """Predicates read runtime fields only; no gold, label, or intent."""

    def test_contradicts_matches_a_negation_pair_over_shared_content(self) -> None:
        runtime = case(
            items=(
                item("a", "dana works at acme corp", rank=0),
                item("b", "dana does not work at acme corp", rank=1),
            )
        )
        start = initial_state_from_runtime_case(runtime)
        program = If(predicate=CONTRADICTS, action=Action(ActionKind.DEMOTE))
        result = execute_program(program, runtime, start, bounds=LOOSE_BOUNDS)
        demoted = {i.item_id for i in result.state.items if i.disposition == "demoted"}
        self.assertEqual(demoted, {"a", "b"})

    def test_contradicts_does_not_match_unrelated_items(self) -> None:
        runtime = case(
            items=(
                item("a", "dana works at acme corp"),
                item("b", "the weather in oslo is cold"),
            )
        )
        start = initial_state_from_runtime_case(runtime)
        program = If(predicate=CONTRADICTS, action=Action(ActionKind.DEMOTE))
        result = execute_program(program, runtime, start, bounds=LOOSE_BOUNDS)
        self.assertEqual(result.state.state_hash, start.state_hash)
        self.assertEqual(result.matched_item_count, 0)

    def test_contradicts_needs_shared_content_not_just_a_negation(self) -> None:
        """Opposite polarity about different subjects is not a contradiction.

        Without a content-overlap requirement every negated sentence in recall
        would collide with every unnegated one, and the predicate would fire on
        an item pair that never competed for the same slot.
        """
        runtime = case(
            items=(
                item("a", "dana works at acme corp"),
                item("b", "no pottery class on friday", rank=1),
            )
        )
        start = initial_state_from_runtime_case(runtime)
        program = If(predicate=CONTRADICTS, action=Action(ActionKind.DEMOTE))
        result = execute_program(program, runtime, start, bounds=LOOSE_BOUNDS)
        self.assertEqual(result.matched_item_count, 0)
        self.assertEqual(result.state.state_hash, start.state_hash)

    def test_query_relevant_matches_only_items_sharing_query_terms(self) -> None:
        runtime = case(
            query="dana employer",
            items=(
                item("a", "dana joined acme"),
                item("b", "unrelated note about pottery"),
            ),
        )
        start = initial_state_from_runtime_case(runtime)
        program = If(predicate=RELEVANT, action=Action(ActionKind.DEMOTE))
        result = execute_program(program, runtime, start, bounds=LOOSE_BOUNDS)
        demoted = {i.item_id for i in result.state.items if i.disposition == "demoted"}
        self.assertEqual(demoted, {"a"})

    def test_not_inverts_the_matched_set(self) -> None:
        runtime = case(
            query="dana employer",
            items=(
                item("a", "dana joined acme"),
                item("b", "unrelated note about pottery"),
            ),
        )
        start = initial_state_from_runtime_case(runtime)
        program = If(
            predicate=Predicate(kind=PredicateKind.NOT, operands=(RELEVANT,)),
            action=Action(ActionKind.DEMOTE),
        )
        result = execute_program(program, runtime, start, bounds=LOOSE_BOUNDS)
        demoted = {i.item_id for i in result.state.items if i.disposition == "demoted"}
        self.assertEqual(demoted, {"b"})

    def test_and_requires_both_operands(self) -> None:
        runtime = case(
            query="dana employer acme",
            items=(
                item("a", "dana works at acme corp"),
                item("b", "dana does not work at acme corp"),
                item("c", "pottery class on tuesday"),
            ),
        )
        start = initial_state_from_runtime_case(runtime)
        program = If(
            predicate=Predicate(
                kind=PredicateKind.AND, operands=(CONTRADICTS, RELEVANT)
            ),
            action=Action(ActionKind.SUPPRESS),
        )
        result = execute_program(program, runtime, start, bounds=LOOSE_BOUNDS)
        suppressed = {
            i.item_id for i in result.state.items if i.disposition == "suppressed"
        }
        self.assertEqual(suppressed, {"a", "b"})

    def test_or_takes_the_union(self) -> None:
        runtime = case(
            query="pottery",
            items=(
                item("a", "dana works at acme corp"),
                item("b", "dana does not work at acme corp"),
                item("c", "pottery class on tuesday"),
            ),
        )
        start = initial_state_from_runtime_case(runtime)
        program = If(
            predicate=Predicate(
                kind=PredicateKind.OR, operands=(CONTRADICTS, RELEVANT)
            ),
            action=Action(ActionKind.DEMOTE),
        )
        result = execute_program(program, runtime, start, bounds=LOOSE_BOUNDS)
        demoted = {i.item_id for i in result.state.items if i.disposition == "demoted"}
        self.assertEqual(demoted, {"a", "b", "c"})

    def test_similarity_above_respects_its_threshold(self) -> None:
        runtime = case(
            items=(
                item("a", "dana works at acme corp in oslo"),
                item("b", "dana works at acme corp in oslo now"),
                item("c", "entirely different subject matter here"),
            )
        )
        start = initial_state_from_runtime_case(runtime)
        program = If(
            predicate=Predicate(
                kind=PredicateKind.SIMILARITY_ABOVE, threshold=0.75
            ),
            action=Action(ActionKind.DEMOTE),
        )
        result = execute_program(program, runtime, start, bounds=LOOSE_BOUNDS)
        demoted = {i.item_id for i in result.state.items if i.disposition == "demoted"}
        self.assertEqual(demoted, {"a", "b"})

    def test_evidence_missing_does_not_fire_when_recall_covers_the_query(self) -> None:
        """A fillable pool must stay untouched while the query is already covered.

        The pool is non-empty on purpose: with nothing to add, the assertion
        would hold no matter what the coverage test decided.
        """
        covered = case(
            query="acme",
            items=(
                item("a", "dana works at acme", rank=0),
                item("pool", "dana also visited globex", rank=1, retrieved=False),
            ),
        )
        start = initial_state_from_runtime_case(covered)
        program = If(predicate=MISSING, action=Action(ActionKind.RETRIEVE_FILL))
        result = execute_program(program, covered, start, bounds=LOOSE_BOUNDS)
        self.assertEqual(result.state.state_hash, start.state_hash)
        self.assertEqual(result.retrieved_additions, 0)

    def test_evidence_missing_fires_when_most_of_the_query_is_uncovered(self) -> None:
        uncovered = case(
            query="dana globex oslo salary",
            items=(
                item("a", "dana likes pottery", rank=0),
                item("pool", "dana moved to globex", rank=1, retrieved=False),
            ),
        )
        start = initial_state_from_runtime_case(uncovered)
        program = If(predicate=MISSING, action=Action(ActionKind.RETRIEVE_FILL))
        result = execute_program(program, uncovered, start, bounds=LOOSE_BOUNDS)
        self.assertEqual(result.retrieved_additions, 1)

    def test_source_more_reliable_reads_the_store_field(self) -> None:
        runtime = case(
            items=(
                item("a", "dana works at acme", store="verified"),
                item("b", "dana works at globex", store="inferred"),
            )
        )
        start = initial_state_from_runtime_case(runtime)
        program = If(
            predicate=Predicate(kind=PredicateKind.SOURCE_MORE_RELIABLE),
            action=Action(ActionKind.DEMOTE),
        )
        result = execute_program(program, runtime, start, bounds=LOOSE_BOUNDS)
        demoted = {i.item_id for i in result.state.items if i.disposition == "demoted"}
        self.assertEqual(demoted, {"b"})

    def test_provenance_matches_reads_source_event_ids(self) -> None:
        runtime = case(
            items=(
                item("a", "dana works at acme", events=("e1",)),
                item("b", "dana relocated", events=()),
            ),
            events=(RuntimeEvent(event_id="e1", text="dana joined acme"),),
        )
        start = initial_state_from_runtime_case(runtime)
        program = If(
            predicate=Predicate(kind=PredicateKind.PROVENANCE_MATCHES),
            action=Action(ActionKind.DEMOTE),
        )
        result = execute_program(program, runtime, start, bounds=LOOSE_BOUNDS)
        demoted = {i.item_id for i in result.state.items if i.disposition == "demoted"}
        self.assertEqual(demoted, {"a"})

    def test_temporal_dominates_needs_a_same_slot_pair(self) -> None:
        """Two items about different subjects do not supersede each other."""
        runtime = case(
            items=(
                item("a", "dana works at acme corp", rank=0),
                item("b", "pottery class on friday evening", rank=1),
            )
        )
        start = initial_state_from_runtime_case(runtime)
        program = If(
            predicate=Predicate(kind=PredicateKind.TEMPORAL_DOMINATES),
            action=Action(ActionKind.DEMOTE),
        )
        result = execute_program(program, runtime, start, bounds=LOOSE_BOUNDS)
        self.assertEqual(result.matched_item_count, 0)

    def test_temporal_dominates_prefers_the_later_ranked_duplicate(self) -> None:
        """Rank order is the only temporal signal a RuntimeMemoryItem carries."""
        runtime = case(
            items=(
                item("old", "dana works at acme corp", rank=0),
                item("new", "dana works at globex corp", rank=1),
            )
        )
        start = initial_state_from_runtime_case(runtime)
        program = If(
            predicate=Predicate(kind=PredicateKind.TEMPORAL_DOMINATES),
            action=Action(ActionKind.DEMOTE),
        )
        result = execute_program(program, runtime, start, bounds=LOOSE_BOUNDS)
        demoted = {i.item_id for i in result.state.items if i.disposition == "demoted"}
        self.assertEqual(demoted, {"new"})


class ActionSemanticsTest(unittest.TestCase):
    def test_suppress_removes_the_item_from_the_rendered_context(self) -> None:
        runtime = case(
            query="acme",
            items=(item("a", "dana works at acme"), item("b", "keep me", rank=1)),
        )
        start = initial_state_from_runtime_case(runtime)
        program = If(predicate=RELEVANT, action=Action(ActionKind.SUPPRESS))
        result = execute_program(program, runtime, start, bounds=LOOSE_BOUNDS)
        self.assertNotIn("dana works at acme", result.state.rendered_context)
        self.assertIn("keep me", result.state.rendered_context)

    def test_demote_keeps_the_item_visible(self) -> None:
        runtime = case(query="acme", items=(item("a", "dana works at acme"),))
        start = initial_state_from_runtime_case(runtime)
        program = If(predicate=RELEVANT, action=Action(ActionKind.DEMOTE))
        result = execute_program(program, runtime, start, bounds=LOOSE_BOUNDS)
        self.assertIn("dana works at acme", result.state.rendered_context)

    def test_retrieve_fill_adds_an_unretrieved_pool_item(self) -> None:
        runtime = case(
            query="globex",
            items=(
                item("a", "dana works at acme", rank=0),
                item("pool", "dana moved to globex", rank=1, retrieved=False),
            ),
        )
        start = initial_state_from_runtime_case(runtime)
        self.assertEqual({i.item_id for i in start.items}, {"a"})
        program = If(predicate=MISSING, action=Action(ActionKind.RETRIEVE_FILL))
        result = execute_program(program, runtime, start, bounds=LOOSE_BOUNDS)
        self.assertIn("pool", {i.item_id for i in result.state.items})
        self.assertEqual(result.retrieved_additions, 1)

    def test_retrieve_fill_never_reintroduces_an_already_present_item(self) -> None:
        runtime = case(
            query="acme",
            items=(item("a", "dana works at acme unknown token"),),
        )
        start = initial_state_from_runtime_case(runtime)
        program = If(predicate=MISSING, action=Action(ActionKind.RETRIEVE_FILL))
        result = execute_program(program, runtime, start, bounds=LOOSE_BOUNDS)
        self.assertEqual(len(result.state.items), 1)

    def test_annotate_conflict_sets_the_conflict_disposition(self) -> None:
        runtime = case(
            items=(
                item("a", "dana works at acme corp"),
                item("b", "dana does not work at acme corp", rank=1),
            )
        )
        start = initial_state_from_runtime_case(runtime)
        program = If(predicate=CONTRADICTS, action=Action(ActionKind.ANNOTATE_CONFLICT))
        result = execute_program(program, runtime, start, bounds=LOOSE_BOUNDS)
        self.assertEqual(
            {i.disposition for i in result.state.items}, {"conflict"}
        )

    def test_replace_demotes_the_matched_item_to_historical(self) -> None:
        """Replace may not write text: §8.2 forbids a literal in the AST."""
        runtime = case(
            query="acme",
            items=(item("a", "dana works at acme"),),
        )
        start = initial_state_from_runtime_case(runtime)
        program = If(predicate=RELEVANT, action=Action(ActionKind.REPLACE))
        result = execute_program(program, runtime, start, bounds=LOOSE_BOUNDS)
        self.assertEqual(
            {i.disposition for i in result.state.items}, {"historical"}
        )

    def test_abstain_leaves_state_untouched_but_is_recorded(self) -> None:
        runtime = case(query="acme", items=(item("a", "dana works at acme"),))
        start = initial_state_from_runtime_case(runtime)
        program = If(predicate=RELEVANT, action=Action(ActionKind.ABSTAIN))
        result = execute_program(program, runtime, start, bounds=LOOSE_BOUNDS)
        self.assertEqual(result.state.state_hash, start.state_hash)
        self.assertTrue(result.abstained)

    def test_verify_leaves_state_untouched_and_costs_a_check(self) -> None:
        runtime = case(query="acme", items=(item("a", "dana works at acme"),))
        start = initial_state_from_runtime_case(runtime)
        program = If(predicate=RELEVANT, action=Action(ActionKind.VERIFY))
        result = execute_program(program, runtime, start, bounds=LOOSE_BOUNDS)
        self.assertEqual(result.state.state_hash, start.state_hash)
        self.assertGreater(result.logical_cost, 0)


class TraceTest(unittest.TestCase):
    """§14.2 every state delta has a trace event."""

    def test_each_firing_rule_appends_exactly_one_event(self) -> None:
        runtime = case(
            query="acme",
            items=(
                item("a", "dana works at acme corp"),
                item("b", "dana does not work at acme corp", rank=1),
            ),
        )
        start = initial_state_from_runtime_case(runtime)
        program = Sequence(
            (
                If(predicate=CONTRADICTS, action=Action(ActionKind.ANNOTATE_CONFLICT)),
                If(predicate=RELEVANT, action=Action(ActionKind.DEMOTE)),
            )
        )
        result = execute_program(program, runtime, start, bounds=LOOSE_BOUNDS)
        self.assertEqual(len(result.state.trace), 2)

    def test_a_rule_that_matches_nothing_appends_no_event(self) -> None:
        runtime = case(query="pottery", items=(item("a", "dana works at acme"),))
        start = initial_state_from_runtime_case(runtime)
        program = If(predicate=RELEVANT, action=Action(ActionKind.DEMOTE))
        result = execute_program(program, runtime, start, bounds=LOOSE_BOUNDS)
        self.assertEqual(result.state.trace, ())

    def test_execution_is_byte_deterministic(self) -> None:
        runtime = case(
            query="acme",
            items=(
                item("a", "dana works at acme corp"),
                item("b", "dana does not work at acme corp", rank=1),
            ),
        )
        start = initial_state_from_runtime_case(runtime)
        program = If(predicate=CONTRADICTS, action=Action(ActionKind.DEMOTE))
        first = execute_program(program, runtime, start, bounds=LOOSE_BOUNDS)
        second = execute_program(program, runtime, start, bounds=LOOSE_BOUNDS)
        self.assertEqual(first.state.state_hash, second.state.state_hash)


class RuntimeBoundsTest(unittest.TestCase):
    """§8.1 the three case-dependent bounds are enforced during execution."""

    def test_exceeding_max_retrieved_additions_fails_closed(self) -> None:
        runtime = case(
            query="globex oslo pottery",
            items=(
                item("a", "unrelated seed", rank=0),
                item("p1", "globex note", rank=1, retrieved=False),
                item("p2", "oslo note", rank=2, retrieved=False),
                item("p3", "pottery note", rank=3, retrieved=False),
            ),
        )
        start = initial_state_from_runtime_case(runtime)
        bounds = ResourceBounds(
            max_depth=8,
            max_nodes=64,
            max_actions_per_case=16,
            max_retrieved_additions=1,
            max_token_delta=4096,
            max_logical_cost=64,
        )
        program = If(predicate=MISSING, action=Action(ActionKind.RETRIEVE_FILL))
        with self.assertRaises(ExecutionLimitError):
            execute_program(program, runtime, start, bounds=bounds)

    def test_exceeding_max_logical_cost_fails_closed(self) -> None:
        runtime = case(
            query="acme",
            items=tuple(
                item(f"i{index}", "dana works at acme", rank=index)
                for index in range(6)
            ),
        )
        start = initial_state_from_runtime_case(runtime)
        bounds = ResourceBounds(
            max_depth=8,
            max_nodes=64,
            max_actions_per_case=16,
            max_retrieved_additions=8,
            max_token_delta=4096,
            max_logical_cost=2,
        )
        program = If(predicate=RELEVANT, action=Action(ActionKind.DEMOTE))
        with self.assertRaises(ExecutionLimitError):
            execute_program(program, runtime, start, bounds=bounds)

    def test_exceeding_max_token_delta_fails_closed(self) -> None:
        runtime = case(
            query="globex",
            items=(
                item("a", "seed", rank=0),
                item("p1", " ".join(["word"] * 50), rank=1, retrieved=False),
            ),
        )
        start = initial_state_from_runtime_case(runtime)
        bounds = ResourceBounds(
            max_depth=8,
            max_nodes=64,
            max_actions_per_case=16,
            max_retrieved_additions=8,
            max_token_delta=5,
            max_logical_cost=64,
        )
        program = If(predicate=MISSING, action=Action(ActionKind.RETRIEVE_FILL))
        with self.assertRaises(ExecutionLimitError):
            execute_program(program, runtime, start, bounds=bounds)

    def test_static_bounds_are_checked_before_execution(self) -> None:
        runtime = case(items=(item("a", "dana works at acme"),))
        start = initial_state_from_runtime_case(runtime)
        bounds = ResourceBounds(
            max_depth=1,
            max_nodes=64,
            max_actions_per_case=16,
            max_retrieved_additions=8,
            max_token_delta=4096,
            max_logical_cost=64,
        )
        program = If(
            predicate=Predicate(
                kind=PredicateKind.AND, operands=(CONTRADICTS, RELEVANT)
            ),
            action=Action(ActionKind.DEMOTE),
        )
        with self.assertRaises(Exception):
            execute_program(program, runtime, start, bounds=bounds)


class GoldSeparationTest(unittest.TestCase):
    """§14.1 execution reads runtime fields only."""

    def test_executor_rejects_a_case_carrying_gold(self) -> None:
        class LeakyCase(RuntimeRepairCase):
            pass

        runtime = case(items=(item("a", "dana works at acme"),))
        leaky = LeakyCase(
            case_id=runtime.case_id,
            family_id=runtime.family_id,
            query=runtime.query,
            items=runtime.items,
            raw_events=runtime.raw_events,
            token_budget=runtime.token_budget,
        )
        object.__setattr__(leaky, "gold_answer", "acme corp")
        start = initial_state_from_runtime_case(runtime)
        with self.assertRaises(Exception):
            execute_program(NULL_PROGRAM, leaky, start, bounds=LOOSE_BOUNDS)


if __name__ == "__main__":
    unittest.main()
