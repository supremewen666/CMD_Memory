"""§9.2's proposal pipeline: the eight steps every proposal passes.

The pipeline is where an open synthesis run either stays inside its declared
space or quietly leaves it. Three properties carry the weight:

  * **The order is load-bearing.** §9.2 lists parse -> type-check ->
    denylist -> canonicalize -> resource-check -> fingerprint -> evaluate ->
    ledger. Evaluating before the resource check would let an over-budget program
    consume evaluator batches; fingerprinting before canonicalization would let
    two spellings of one program occupy two behavior classes.
  * **Every rejection is ledgered.** §9.2 appends *every* proposed program to an
    immutable ledger. A pipeline that only records what survived would report a
    150-proposal seed as however many proposals happened to compile, and the
    acceptance rate -- the one number that says whether the proposer understood
    the grammar -- would be unrecoverable.
  * **Rejection reasons never carry case content.** §9.2 permits the proposer to
    read "static compile/runtime errors without case content". An error string
    that quoted the offending memory ID would be a leak through the one channel
    the contract leaves open.

The ledger is also the E3 budget's only enforcement point, so the counter is
pinned separately: §16 fixes 150 proposals per seed, and a pipeline that counted
only accepted proposals could run indefinitely.
"""

import unittest

from cmd_audit.counterfactual.program_ir import (
    REGISTERED_BOUNDS,
    Action,
    ActionKind,
    If,
    Predicate,
    PredicateKind,
    Sequence,
    canonical_ast_hash,
    canonicalize,
    parse_program,
    program_depth,
    program_to_mapping,
)
from cmd_audit.counterfactual.proposal_pipeline import (
    MAX_PROPOSALS_PER_SEED,
    PIPELINE_STAGES,
    ProposalOutcome,
    ProposalPipeline,
    RejectionStage,
    proposal_ledger_row,
)


def _program(kind=PredicateKind.CONTRADICTS, action=ActionKind.SUPPRESS):
    return Sequence((If(predicate=Predicate(kind=kind), action=Action(action)),))


def _mapping(kind=PredicateKind.CONTRADICTS, action=ActionKind.SUPPRESS):
    return program_to_mapping(_program(kind, action))


class StageOrderTest(unittest.TestCase):
    """§9.2's list is a sequence, not a set."""

    def test_the_stages_are_the_eight_the_spec_names_in_order(self) -> None:
        self.assertEqual(
            PIPELINE_STAGES,
            (
                "parse",
                "type_check",
                "denylist",
                "canonicalize",
                "resource_check",
                "fingerprint",
                "evaluate",
                "ledger",
            ),
        )

    def test_the_resource_check_precedes_evaluation(self) -> None:
        """An over-budget program must not reach the evaluator: batches are the
        scarce resource §16 bounds, and spending one on a program that cannot
        legally execute buys nothing."""
        self.assertLess(
            PIPELINE_STAGES.index("resource_check"), PIPELINE_STAGES.index("evaluate")
        )

    def test_canonicalization_precedes_fingerprinting(self) -> None:
        """Two spellings of one program share a canonical hash. Fingerprinting
        first would let them occupy two behavior classes and inflate the novelty
        §11.3 requires the artifact to demonstrate."""
        self.assertLess(
            PIPELINE_STAGES.index("canonicalize"), PIPELINE_STAGES.index("fingerprint")
        )

    def test_the_ledger_is_last_so_it_records_every_earlier_outcome(self) -> None:
        self.assertEqual(PIPELINE_STAGES[-1], "ledger")


class AcceptanceTest(unittest.TestCase):
    def test_a_legal_program_is_accepted_with_its_canonical_hash(self) -> None:
        pipeline = ProposalPipeline(seed=11)
        outcome = pipeline.submit(_mapping())
        self.assertTrue(outcome.accepted, outcome.reason)
        self.assertEqual(
            outcome.canonical_ast_hash, canonical_ast_hash(canonicalize(_program()))
        )
        self.assertIsNone(outcome.rejected_at)

    def test_an_accepted_proposal_carries_a_behavior_fingerprint(self) -> None:
        """§9.2 step 6. The fingerprint is what §11.3's novelty test compares
        against the pre-search envelope, so an accepted proposal without one
        could not be shown to be novel."""
        outcome = ProposalPipeline(seed=11).submit(_mapping())
        self.assertTrue(outcome.behavior_fingerprint)

    def test_two_spellings_of_one_program_share_a_canonical_hash(self) -> None:
        """Canonicalization runs before the hash, so a commutative reordering is
        the same proposal. Otherwise a proposer could exhaust its budget
        resubmitting one program under different spellings."""
        left = Predicate(kind=PredicateKind.CONTRADICTS)
        right = Predicate(kind=PredicateKind.QUERY_RELEVANT)
        forward = Sequence(
            (
                If(
                    predicate=Predicate(kind=PredicateKind.AND, operands=(left, right)),
                    action=Action(ActionKind.SUPPRESS),
                ),
            )
        )
        backward = Sequence(
            (
                If(
                    predicate=Predicate(kind=PredicateKind.AND, operands=(right, left)),
                    action=Action(ActionKind.SUPPRESS),
                ),
            )
        )
        pipeline = ProposalPipeline(seed=11)
        first = pipeline.submit(program_to_mapping(forward))
        second = pipeline.submit(program_to_mapping(backward))
        self.assertTrue(first.accepted, first.reason)
        self.assertTrue(second.accepted, second.reason)
        self.assertEqual(first.canonical_ast_hash, second.canonical_ast_hash)


class RejectionTest(unittest.TestCase):
    """Each stage's own refusal, named so the ledger says which one fired."""

    def test_unparseable_text_is_rejected_at_parse(self) -> None:
        outcome = ProposalPipeline(seed=11).submit("not a program")
        self.assertFalse(outcome.accepted)
        self.assertEqual(outcome.rejected_at, RejectionStage.PARSE)

    def test_an_unknown_predicate_kind_is_rejected(self) -> None:
        payload = _mapping()
        payload["body"][0]["predicate"]["kind"] = "invented_predicate"
        outcome = ProposalPipeline(seed=11).submit(payload)
        self.assertFalse(outcome.accepted)

    def test_a_literal_case_field_is_rejected_at_the_denylist(self) -> None:
        """§8.2: the typed IR cannot carry a case literal. The allowed-key set is
        exact, so an invented key has nowhere to live -- this is the check that
        keeps a proposer from writing a memory ID into an AST."""
        payload = _mapping()
        payload["body"][0]["predicate"]["target_item_id"] = "m_gold_1"
        outcome = ProposalPipeline(seed=11).submit(payload)
        self.assertFalse(outcome.accepted)
        self.assertIn(outcome.rejected_at, (RejectionStage.PARSE, RejectionStage.DENYLIST))

    def test_an_over_depth_program_is_rejected_at_the_resource_check(self) -> None:
        """The bound applies to the *canonical* program, so the nesting has to
        survive canonicalization.

        A tower of `not`s collapses (5 -> 1) and same-connective nesting flattens
        (`and(and(a, b), c)` -> `and(a, b, c)`), so both of those are legitimately
        in bounds however deep they are written. Alternating `and`/`or` cannot
        flatten -- the connectives differ -- so this is what an actually
        over-depth proposal looks like.
        """
        predicate = Predicate(kind=PredicateKind.CONTRADICTS)
        alternating = (PredicateKind.OR, PredicateKind.AND)
        extra_leaves = (
            PredicateKind.QUERY_RELEVANT,
            PredicateKind.EVIDENCE_MISSING,
            PredicateKind.TEMPORAL_DOMINATES,
            PredicateKind.SOURCE_MORE_RELIABLE,
        )
        for index, leaf in enumerate(extra_leaves):
            predicate = Predicate(
                kind=alternating[index % 2],
                operands=(predicate, Predicate(kind=leaf)),
            )
        payload = program_to_mapping(
            Sequence((If(predicate=predicate, action=Action(ActionKind.SUPPRESS)),))
        )
        self.assertGreater(
            program_depth(canonicalize(parse_program(payload))),
            REGISTERED_BOUNDS.max_depth,
            "fixture no longer exceeds the bound; canonicalization changed",
        )
        outcome = ProposalPipeline(seed=11).submit(payload)
        self.assertFalse(outcome.accepted)
        self.assertEqual(outcome.rejected_at, RejectionStage.RESOURCE_CHECK)

    def test_a_deeply_written_but_collapsible_program_is_accepted(self) -> None:
        """The other side of the same edge, and the reason the test above needs
        alternating connectives.

        Five stacked `not`s canonicalize to one, so this program is inside the
        registered space no matter how it was written. Rejecting on the *written*
        depth would refuse legal programs and would make the resource check
        depend on spelling -- the thing canonicalization exists to remove.
        """
        deep = Predicate(kind=PredicateKind.CONTRADICTS)
        for _ in range(REGISTERED_BOUNDS.max_depth + 2):
            deep = Predicate(kind=PredicateKind.NOT, operands=(deep,))
        payload = program_to_mapping(
            Sequence((If(predicate=deep, action=Action(ActionKind.SUPPRESS)),))
        )
        outcome = ProposalPipeline(seed=11).submit(payload)
        self.assertTrue(outcome.accepted, outcome.reason)

    def test_an_identity_only_program_is_rejected_and_named_as_such(self) -> None:
        """A program whose every action is `keep`/`preserve` cannot change a
        state, so evaluating it spends a batch to measure the null program that
        is already in the initial population under its own name.

        The stage matters as much as the refusal: `canonicalize` raises here, and
        reporting that stage would tell a proposer to look for a malformed AST
        when the actual problem is that its program does nothing.
        """
        outcome = ProposalPipeline(seed=11).submit(_mapping(action=ActionKind.KEEP))
        self.assertFalse(outcome.accepted)
        self.assertEqual(outcome.rejected_at, RejectionStage.IDENTITY)
        self.assertIn("identity action", outcome.reason)

    def test_a_program_with_one_acting_rule_among_identities_is_accepted(self) -> None:
        """The other side of the identity check. §9.1 excludes programs that
        cannot act, not programs that sometimes decline to -- a rule that keeps
        under one predicate and suppresses under another is a real strategy, and
        refusing it would shrink the searchable space to programs that always
        act.
        """
        payload = program_to_mapping(
            Sequence(
                (
                    If(
                        predicate=Predicate(kind=PredicateKind.QUERY_RELEVANT),
                        action=Action(ActionKind.KEEP),
                    ),
                    If(
                        predicate=Predicate(kind=PredicateKind.CONTRADICTS),
                        action=Action(ActionKind.SUPPRESS),
                    ),
                )
            )
        )
        outcome = ProposalPipeline(seed=11).submit(payload)
        self.assertTrue(outcome.accepted, outcome.reason)

    def test_a_non_numeric_threshold_is_rejected_without_echoing_it(self) -> None:
        """`parse_program` raises a bare `ValueError` here, not a
        `ProgramParseError`, and its message quotes the offending value.

        Both halves matter. Catching only `ProgramParseError` would *accept* this
        program -- the exception would escape `submit` and crash the seed, or
        worse be caught upstream as a run failure. And the value is proposer-
        supplied text that lands in a reason string the proposer reads back, so
        the sanitizer has to drop it.
        """
        payload = _mapping()
        payload["body"][0]["predicate"]["threshold"] = "gold_answer_is_Austin"
        outcome = ProposalPipeline(seed=11).submit(payload)
        self.assertFalse(outcome.accepted)
        self.assertEqual(outcome.rejected_at, RejectionStage.PARSE)
        self.assertNotIn("Austin", outcome.reason)

    def test_a_rejection_reason_never_quotes_case_content(self) -> None:
        """§9.2 lets the proposer read static errors "without case content". The
        payload here carries a memory ID and a required phrase; neither may
        appear in the reason the proposer is shown.
        """
        payload = _mapping()
        payload["body"][0]["predicate"]["source_memory_id"] = "m_secret_gold_42"
        payload["body"][0]["required_phrases"] = ["moved to Austin"]
        outcome = ProposalPipeline(seed=11).submit(payload)
        self.assertFalse(outcome.accepted)
        self.assertNotIn("m_secret_gold_42", outcome.reason)
        self.assertNotIn("Austin", outcome.reason)


class FrozenBudgetTest(unittest.TestCase):
    """§16's per-seed cap, pinned as a value.

    Every other budget test passes an explicit `max_proposals`, so the default
    could drift to any number and only this test would notice. §16 fixes it at
    150 and §9.1 repeats it, which makes it a preregistered parameter rather than
    a tuning knob.
    """

    def test_the_default_per_seed_budget_is_the_registered_150(self) -> None:
        self.assertEqual(MAX_PROPOSALS_PER_SEED, 150)
        self.assertEqual(ProposalPipeline(seed=11).max_proposals, 150)

    def test_a_seed_with_no_budget_is_refused_at_construction(self) -> None:
        """A pipeline with a zero or negative budget is exhausted before its
        first proposal, so every submission raises. Constructing it and failing
        later would report a configuration error as a search that found nothing.
        """
        with self.assertRaises(ValueError):
            ProposalPipeline(seed=11, max_proposals=0)
        with self.assertRaises(ValueError):
            ProposalPipeline(seed=11, max_proposals=-1)


class LedgerTest(unittest.TestCase):
    """§9.2 step 8: *every* proposal is appended, accepted or not."""

    def test_a_rejected_proposal_is_still_ledgered(self) -> None:
        """The acceptance rate is the one number that says whether the proposer
        understood the grammar. Recording only survivors makes it unrecoverable
        and reports a 150-proposal seed as however many happened to compile."""
        pipeline = ProposalPipeline(seed=11)
        pipeline.submit("not a program")
        pipeline.submit(_mapping())
        self.assertEqual(len(pipeline.ledger), 2)
        self.assertEqual([row.accepted for row in pipeline.ledger], [False, True])

    def test_the_ledger_counts_toward_the_budget_on_rejection(self) -> None:
        """§16 caps proposals per seed. Counting only accepted ones would let a
        proposer that emits garbage run without bound."""
        pipeline = ProposalPipeline(seed=11, max_proposals=3)
        for _ in range(3):
            pipeline.submit("not a program")
        self.assertTrue(pipeline.exhausted)

    def test_submitting_past_the_budget_is_refused(self) -> None:
        pipeline = ProposalPipeline(seed=11, max_proposals=1)
        pipeline.submit(_mapping())
        with self.assertRaises(RuntimeError):
            pipeline.submit(_mapping(kind=PredicateKind.QUERY_RELEVANT))

    def test_the_ledger_is_append_only(self) -> None:
        """§9.2 calls the ledger immutable. Handing out the live list would let a
        caller drop a rejection after the fact."""
        pipeline = ProposalPipeline(seed=11)
        pipeline.submit(_mapping())
        snapshot = pipeline.ledger
        with self.assertRaises((AttributeError, TypeError)):
            snapshot.append("forged")  # type: ignore[attr-defined]

    def test_a_duplicate_proposal_is_ledgered_and_marked(self) -> None:
        """Resubmitting a canonical hash already seen is not an error -- the
        proposer cannot see the ledger -- but it must not be counted as a new
        behavior class, and the ledger has to show it happened so the effective
        exploration of a seed is recoverable from the artifact.
        """
        pipeline = ProposalPipeline(seed=11)
        first = pipeline.submit(_mapping())
        second = pipeline.submit(_mapping())
        self.assertTrue(first.accepted)
        self.assertTrue(second.duplicate)
        self.assertEqual(len(pipeline.ledger), 2)
        self.assertEqual(len(pipeline.unique_accepted), 1)

    def test_a_ledger_row_records_the_stage_and_carries_no_case_content(self) -> None:
        pipeline = ProposalPipeline(seed=11)
        pipeline.submit("not a program")
        row = proposal_ledger_row(pipeline.ledger[0], index=0)
        self.assertEqual(row["rejected_at"], RejectionStage.PARSE.value)
        self.assertFalse(row["accepted"])
        self.assertEqual(row["seed"], 11)
        self.assertIn("reason", row)


class DeterminismTest(unittest.TestCase):
    def test_the_same_submissions_under_one_seed_give_the_same_ledger(self) -> None:
        payloads = [_mapping(), "not a program", _mapping(kind=PredicateKind.QUERY_RELEVANT)]
        rows = []
        for _ in range(2):
            pipeline = ProposalPipeline(seed=11)
            for payload in payloads:
                pipeline.submit(payload)
            rows.append(
                [proposal_ledger_row(row, index=i) for i, row in enumerate(pipeline.ledger)]
            )
        self.assertEqual(rows[0], rows[1])


class OutcomeTest(unittest.TestCase):
    def test_an_outcome_is_frozen(self) -> None:
        """The ledger is immutable, so its rows have to be too."""
        outcome = ProposalPipeline(seed=11).submit(_mapping())
        with self.assertRaises(Exception):
            outcome.accepted = False  # type: ignore[misc]
        self.assertIsInstance(outcome, ProposalOutcome)


if __name__ == "__main__":
    unittest.main()
