"""Route A E-1 tests: development intent adapter (BUILD SPEC §3.3, §14.1).

Expected values come from the on-disk probe datasets inspected directly, not
from the adapter's own logic: memtrace item_stale keeps ``m_kp`` as gold with
``m_prior`` as the superseded competitor, memtrace retrieval_error inverts that
(gold is ``m_prior``), memfail item_conflict keeps three coexisting preferences
against an over-merged ``m_reconciled``, and unlabeled memtrace cases carry no
perturbation at all.
"""

import unittest

from cmd_audit.core.models import (
    BaselineOutput,
    GoldEvidence,
    MemoryItem,
    ProbeCase,
    RawEvent,
)
from cmd_audit.eval.dev_state_intents import (
    IntentConstructionError,
    build_dev_intent,
    build_dev_intents,
    family_id_for_case,
)


def case(
    *,
    case_id="c1",
    label=None,
    items,
    gold,
    gold_answer="answer",
    query="q",
    recall=None,
    safety_blocked=False,
) -> ProbeCase:
    """Build a probe case. ``recall`` defaults to every item, matching the
    stale_item dataset where pool == recall for all 1200 cases; pass an explicit
    subset for the memfail/memtrace shapes where the retriever missed gold."""
    recalled = (
        tuple(memory_id for memory_id, _, _ in items) if recall is None else recall
    )
    return ProbeCase(
        case_id=case_id,
        query=query,
        raw_events=(RawEvent(event_id="e1", text="event text"),),
        extracted_memory=tuple(
            MemoryItem(
                memory_id=memory_id,
                text=text,
                source_event_ids=("e1",),
                store="episodic",
                passed_safety_filter=safety,
            )
            for memory_id, text, safety in items
        ),
        gold_evidence=tuple(
            GoldEvidence(
                evidence_id=f"ev_{memory_id}",
                text=text,
                source_memory_id=memory_id,
                source_event_id="e1",
                required_phrases=phrases,
            )
            for memory_id, text, phrases in gold
        ),
        gold_answer=gold_answer,
        baseline_outputs=(
            BaselineOutput(
                baseline_name="primary",
                answer="wrong",
                retrieved_memory_ids=recalled,
                answer_score=0.0,
                evidence_score=0.0,
            ),
        ),
        perturbation_label=label,
        safety_filter_blocked=safety_blocked,
    )


STALE_CASE = case(
    case_id="stale-abc-dim1",
    label="item_stale",
    items=(
        ("m_stale", "M_old: I've been based in Seattle for the last few years.", False),
        ("m_current", "M_new: I just moved to Austin.", False),
        ("m_haystack", "[user] Looking for healthy meal prep ideas.", False),
    ),
    gold=(("m_current", "M_new: I just moved to Austin.", ("moved to Austin",)),),
)

RETRIEVAL_CASE = case(
    case_id="memtraceb-2f1f897e-kp0110-a3c0-prior-present",
    label="retrieval_error",
    items=(
        ("m_kp", "Martin has modified his pet preference to Golden Retrievers.", False),
        ("m_prior", "Martin Mark Pets I like: Dogs, especially Labradors", False),
        ("m_sib0", "Martin appreciates Golden Retrievers for their calm demeanor.", False),
    ),
    gold=(
        (
            "m_prior",
            "Martin Mark Pets I like: Dogs, especially Labradors",
            ("Labradors",),
        ),
    ),
)

CONFLICT_CASE = case(
    case_id="memfail-coexisting-hat-styles-q0",
    label="item_conflict",
    items=(
        ("m_pref1", "I often wear a fedora with a classic outfit.", False),
        ("m_pref2", "Beanies are my go-to for chilly days.", False),
        ("m_pref3", "A bucket hat is what I reach for on sunny weekends.", False),
        ("m_reconciled", "Single surviving hat styles preference: bucket hat.", False),
    ),
    gold=(
        ("m_pref1", "fedora", ("fedora",)),
        # Real memfail data: gold says "beanie", the item says "Beanies".
        ("m_pref2", "beanie", ("beanie",)),
        ("m_pref3", "bucket hat", ("bucket hat",)),
    ),
)

NULL_CASE = case(
    case_id="memtraceb-2f1f897e-kp0000-a7c0-current-present",
    label=None,
    items=(
        ("m_kp", "User's name is Martin Mark", False),
        ("m_sib0", "Martin Mark's gender is Male", False),
    ),
    gold=(("m_kp", "User's name is Martin Mark", ("User's name is Martin Mark",)),),
)

# Real memfail safety shape: safety_filter_blocked is True and the flagged item
# is gold itself, so the safety layer removed the answer from context.
REDACTED_GOLD_CASE = case(
    case_id="memfail-persona-noah-brooks-0000-q0",
    label="safety_error",
    items=(
        ("m_profile1", "Yuki Tanaka is a cartographer.", False),
        ("m_no_record", "I don't have information about Noah Brooks.", True),
    ),
    gold=(
        (
            "m_no_record",
            "I don't have information about Noah Brooks.",
            ("I don't have information about Noah Brooks",),
        ),
    ),
    safety_blocked=True,
)


SAFETY_CASE = case(
    case_id="memfail-persona-yuki-tanaka-0000-q0",
    label="safety_error",
    items=(
        ("m_profile1", "Yuki Tanaka is a cartographer.", False),
        ("m_no_record", "I don't have information about Noah Brooks.", True),
    ),
    gold=(
        (
            "m_no_record",
            "I don't have information about Noah Brooks.",
            ("I don't have information about Noah Brooks",),
        ),
    ),
)


# Real memfail granularity_error shape: an over-compressed session summary
# drops the rule's condition clause, and the full rule carries it.
GRANULARITY_CASE = case(
    case_id="memfail-conditional_easy-selene-0000-q0",
    label="granularity_error",
    items=(
        (
            "m_session_summary",
            "Session summary about Selene: Selene composes cryptic messages in a "
            "hidden journal.",
            False,
        ),
        (
            "m_rule_full",
            "Selene composes cryptic messages in a hidden journal when feeling "
            "nostalgic.",
            False,
        ),
    ),
    gold=(
        (
            "m_rule_full",
            "Selene composes cryptic messages when feeling nostalgic.",
            ("when feeling nostalgic",),
        ),
    ),
)

# Real memfail persona shape: the retriever surfaced only m_profile1 while gold
# m_profile3 stayed in the candidate pool. Verified on disk for 143 cases.
RETRIEVAL_MISS_CASE = case(
    case_id="memfail-persona-yuki-tanaka-0000-q2",
    label="retrieval_error",
    items=(
        ("m_profile1", "Yuki Tanaka is a maritime archaeologist.", False),
        ("m_profile2", "Yuki surveys wrecks in the Seto Inland Sea.", False),
        ("m_profile3", "She is allergic to shellfish.", False),
    ),
    gold=(("m_profile3", "She is allergic to shellfish.", ("allergic to shellfish",)),),
    recall=("m_profile1",),
)


# Real memtrace retrieval_error shape where every non-gold item is a sibling
# fact rather than a competing claim about the queried slot.
SIBLING_ONLY_CASE = case(
    case_id="memfail-persona-felix-andersen-0001-q0",
    label="retrieval_error",
    items=(
        ("m_profile1", "Felix Andersen is an amateur astronomer.", False),
        (
            "m_profile2",
            "Felix uses a 12-inch Dobsonian and a little box of sliced Swiss "
            "cheese for snack breaks between alignments.",
            False,
        ),
        ("m_profile3", "Felix refuses to use a phone for star charts.", False),
    ),
    gold=(
        (
            "m_profile2",
            "sliced Swiss cheese",
            ("a little box of sliced Swiss cheese for snack breaks",),
        ),
    ),
)


class RequiredItemTest(unittest.TestCase):
    def test_gold_evidence_becomes_required_items(self):
        intent = build_dev_intent(STALE_CASE, token_budget=256)
        self.assertEqual(
            tuple(r.source_memory_id for r in intent.required_items), ("m_current",)
        )
        self.assertEqual(intent.required_items[0].required_phrases, ("moved to Austin",))
        self.assertEqual(intent.protected_item_ids, ("m_current",))

    def test_multi_gold_case_requires_every_coexisting_item(self):
        intent = build_dev_intent(CONFLICT_CASE, token_budget=256)
        self.assertEqual(
            tuple(r.source_memory_id for r in intent.required_items),
            ("m_pref1", "m_pref2", "m_pref3"),
        )


    def test_required_phrases_match_case_insensitively(self):
        """Project convention is casefold matching (cmd_audit/scoring/phrase.py).

        Real data depends on it: memfail gold phrase 'beanie' appears in the
        item as 'Beanies'.
        """
        intent = build_dev_intent(CONFLICT_CASE, token_budget=256)
        by_id = {r.source_memory_id: r for r in intent.required_items}
        self.assertEqual(by_id["m_pref2"].required_phrases, ("beanie",))


class PerturbationTest(unittest.TestCase):
    def test_superseded_competitor_is_the_perturbation_target(self):
        intent = build_dev_intent(STALE_CASE, token_budget=256)
        self.assertEqual(
            tuple(p.target_item_id for p in intent.perturbations), ("m_stale",)
        )
        self.assertEqual(
            intent.perturbations[0].allowed_resolutions,
            ("demoted", "historical", "suppressed", "removed"),
        )

    def test_retrieval_error_targets_the_currently_surfaced_item(self):
        """Gold is the prior value, so the current item is what must yield."""
        intent = build_dev_intent(RETRIEVAL_CASE, token_budget=256)
        self.assertEqual(
            tuple(p.target_item_id for p in intent.perturbations), ("m_kp",)
        )

    def test_over_merged_item_is_the_perturbation_in_a_conflict_case(self):
        intent = build_dev_intent(CONFLICT_CASE, token_budget=256)
        self.assertEqual(
            tuple(p.target_item_id for p in intent.perturbations), ("m_reconciled",)
        )

    def test_haystack_item_is_not_a_perturbation(self):
        intent = build_dev_intent(STALE_CASE, token_budget=256)
        targets = {p.target_item_id for p in intent.perturbations}
        self.assertNotIn("m_haystack", targets)

    def test_over_compressed_summary_is_the_perturbation(self):
        """A granularity fault's competitor is the lossy summary item.

        Without this, the case has no perturbation and a no-op would score
        state_success = 1 — the abstain_preserve domination §3.4 forbids.
        """
        intent = build_dev_intent(GRANULARITY_CASE, token_budget=256)
        self.assertEqual(
            tuple(p.target_item_id for p in intent.perturbations),
            ("m_session_summary",),
        )

    def test_retrieval_miss_requires_gold_that_was_never_recalled(self):
        """Real memfail persona shape: the retriever surfaced m_profile1 and
        left gold m_profile3 in the pool.

        The fault is the absence itself, so no competitor is needed: gold is not
        in the initial state, so preserve_gold already fails an untouched state
        and the repair must pull the item in.
        """
        intent = build_dev_intent(RETRIEVAL_MISS_CASE, token_budget=256)
        self.assertFalse(intent.null_case)
        self.assertEqual(intent.perturbations, ())
        self.assertEqual(
            tuple(r.source_memory_id for r in intent.required_items), ("m_profile3",)
        )

    def test_a_labeled_case_without_an_identifiable_target_fails(self):
        """A labeled case must not silently become a free no-op win."""
        with self.assertRaises(IntentConstructionError):
            build_dev_intent(SIBLING_ONLY_CASE, token_budget=256)

    def test_unlabeled_case_is_a_null_case_with_no_perturbation(self):
        intent = build_dev_intent(NULL_CASE, token_budget=256)
        self.assertTrue(intent.null_case)
        self.assertEqual(intent.perturbations, ())

    def test_redacted_gold_is_an_active_repair_not_an_abstention(self):
        """Real memfail safety shape: safety_filter_blocked is True and the
        flagged item IS gold (verified 157/157 on disk).

        The safety layer removed the answer from context, so the state is wrong
        and the repair must restore the item. Scoring this as a null case would
        make changing nothing the correct answer.
        """
        intent = build_dev_intent(REDACTED_GOLD_CASE, token_budget=256)
        self.assertFalse(intent.null_case)
        self.assertIn("m_no_record", intent.allowed_added_item_ids)

    def test_abstention_case_is_preserve_only_and_scored_as_null(self):
        """A safety case's memory state is already correct.

        Gold is the scope statement itself and the other items are valid
        memory, so the right repair changes nothing. Marking it null_case is
        what stops a no-op from counting as an active repair win: it is scored
        under the byte-identity rule, not resolve_perturbation.
        """
        intent = build_dev_intent(SAFETY_CASE, token_budget=256)
        self.assertEqual(intent.perturbations, ())
        self.assertTrue(intent.null_case)
        self.assertIn("m_no_record", intent.protected_item_ids)


class IntentBoundaryTest(unittest.TestCase):
    def test_provenance_hashes_bind_every_protected_item(self):
        intent = build_dev_intent(STALE_CASE, token_budget=256)
        self.assertEqual(
            tuple(item_id for item_id, _ in intent.required_provenance_hashes),
            ("m_current",),
        )

    def test_gold_referring_to_an_absent_item_fails_instead_of_dropping(self):
        """§3.3: cases may not be silently dropped."""
        broken = case(
            case_id="broken",
            label="item_stale",
            items=(("m_a", "text a", False),),
            gold=(("m_absent", "missing", ("missing",)),),
        )
        with self.assertRaises(IntentConstructionError):
            build_dev_intent(broken, token_budget=256)


class FamilyKeyTest(unittest.TestCase):
    def test_family_keys_group_paraphrase_variants(self):
        self.assertEqual(
            family_id_for_case("stale-7c0ae4e7-6b5a-42a2-891b-0ccf-dim1"),
            family_id_for_case("stale-7c0ae4e7-6b5a-42a2-891b-0ccf-dim3"),
        )
        self.assertEqual(
            family_id_for_case("memfail-coexisting-hat-styles-q0"),
            family_id_for_case("memfail-coexisting-hat-styles-q1"),
        )
        self.assertEqual(
            family_id_for_case("memtraceb-2f1f897e-kp0000-a7c0-current-present"),
            family_id_for_case("memtraceb-2f1f897e-kp0000-a15c1-current-present"),
        )

    def test_distinct_families_do_not_collide(self):
        self.assertNotEqual(
            family_id_for_case("memfail-coexisting-hat-styles-q0"),
            family_id_for_case("memfail-coexisting-shoe-styles-q0"),
        )


class CoverageReportTest(unittest.TestCase):
    def test_report_counts_every_case_and_records_reason_codes(self):
        report = build_dev_intents(
            (STALE_CASE, NULL_CASE, CONFLICT_CASE), domain="mixed", token_budget=256
        )
        self.assertEqual(report.total_runtime_cases, 3)
        self.assertEqual(report.intents_constructed, 3)
        self.assertEqual(report.one_to_one_joins, 3)
        self.assertEqual(report.intent_constructibility_rate, 1.0)
        self.assertEqual(report.invalid_cases, ())
        self.assertTrue(report.eligible)

    def test_a_domain_with_an_unconstructible_case_is_ineligible(self):
        broken = case(
            case_id="broken-q0",
            label="item_stale",
            items=(("m_a", "text a", False),),
            gold=(("m_absent", "missing", ("missing",)),),
        )
        report = build_dev_intents(
            (STALE_CASE, broken), domain="mixed", token_budget=256
        )
        self.assertEqual(report.total_runtime_cases, 2)
        self.assertEqual(report.intents_constructed, 1)
        self.assertLess(report.intent_constructibility_rate, 1.0)
        self.assertFalse(report.eligible)
        self.assertEqual(len(report.invalid_cases), 1)
        self.assertEqual(report.invalid_cases[0][0], "broken-q0")


if __name__ == "__main__":
    unittest.main()
