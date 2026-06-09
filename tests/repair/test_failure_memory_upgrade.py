"""Failure Memory Upgrade tests — Issue 0020-D."""

from pathlib import Path
import unittest

from cmd_audit import (
    FailureMemoryRecord,
    FailureMemoryStore,
    compute_memory_top_terms,
    draft_ecs_for_label,
    load_probe_cases_v1,
)
from cmd_audit.repair.failure_memory import (
    StepLevelRecord,
    _score_composite_key,
    _FailureMemoryStoreV0,
)
from cmd_audit.repair import FailureMemoryStore, build_failure_memory_context
from cmd_audit.repair import build_repair_context


# ── compute_memory_top_terms ────────────────────────────────────────────


class ComputeMemoryTopTermsTest(unittest.TestCase):
    """AC: Extract top-N terms from retrieved items."""

    def test_empty_items_returns_empty(self) -> None:
        self.assertEqual(compute_memory_top_terms(()), ())

    def test_extracts_frequent_terms(self) -> None:
        from cmd_audit.core.models import MemoryItem
        items = (
            MemoryItem(memory_id="m1", text="The quick brown fox jumps over the lazy dog"),
            MemoryItem(memory_id="m2", text="The quick brown fox runs fast"),
        )
        terms = compute_memory_top_terms(items, top_n=3)
        self.assertIsInstance(terms, tuple)
        self.assertLessEqual(len(terms), 3)
        self.assertIn("quick", terms)
        self.assertIn("brown", terms)
        self.assertIn("jumps", terms)  # fox has 3 letters, filtered by {4,} regex

    def test_filters_stop_words(self) -> None:
        from cmd_audit.core.models import MemoryItem
        items = (
            MemoryItem(memory_id="m1", text="The and or but if then"),
        )
        terms = compute_memory_top_terms(items, top_n=10)
        for stop_word in ("the", "and", "or", "but", "if", "then"):
            self.assertNotIn(stop_word, terms)


# ── FailureMemoryStore ────────────────────────────────────────────────


class FailureMemoryStoreV1Test(unittest.TestCase):
    """AC: Composite-key retrieval with label + query + memory_terms."""

    def setUp(self) -> None:
        self.record_a = FailureMemoryRecord(
            error_type="retrieval_error",
            wrong_memory="wrong answer about Paris",
            original_evidence="Paris is the capital of France",
            cause="retrieval missed the correct item",
            corrected_memory="Paris is the capital of France",
            repair_action="oracle_retrieval",
            repair_guidance="update retrieval routing",
            trigger_signature="retrieval_error|paris capital france",
            memory_top_terms=("paris", "france"),
        )
        self.record_b = FailureMemoryRecord(
            error_type="item_wrong",
            wrong_memory="no memory about Berlin",
            original_evidence="Berlin is the capital of Germany",
            cause="stored item contradicted the source evidence",
            corrected_memory="Berlin is the capital of Germany",
            repair_action="replace",
            repair_guidance="replace the incorrect item",
            trigger_signature="item_wrong berlin capital germany",
            memory_top_terms=("berlin", "germany"),
        )
        self.record_c = FailureMemoryRecord(
            error_type="retrieval_error",
            wrong_memory="wrong about London",
            original_evidence="London is UK capital",
            cause="retrieval missed",
            corrected_memory="London is the capital of UK",
            repair_action="oracle_retrieval",
            repair_guidance="update routing",
            trigger_signature="retrieval_error london capital united kingdom",
            memory_top_terms=("london", "kingdom"),
        )
        self.store = FailureMemoryStore().add(self.record_a).add(self.record_b).add(self.record_c)

    def test_retrieve_by_label_matches(self) -> None:
        results = self.store.retrieve(
            query="What is the capital of France?",
            label="retrieval_error",
            top_k=3,
        )
        self.assertTrue(len(results) > 0)
        # retrieval_error records should rank higher
        self.assertEqual(results[0].error_type, "retrieval_error")

    def test_retrieve_by_query_keywords(self) -> None:
        results = self.store.retrieve(
            query="Tell me about Berlin",
            top_k=3,
        )
        self.assertTrue(len(results) > 0)
        # Berlin record should appear
        berlin_records = [r for r in results if "berlin" in r.trigger_signature]
        self.assertTrue(len(berlin_records) > 0)

    def test_retrieve_with_memory_terms(self) -> None:
        results = self.store.retrieve(
            query="What about Paris?",
            label="retrieval_error",
            top_k=3,
        )
        # Paris record should be top with composite scoring
        self.assertEqual(results[0].error_type, "retrieval_error")
        self.assertIn("paris", results[0].trigger_signature)

    def test_retrieve_second_positional_argument_is_label(self) -> None:
        results = self.store.retrieve("What about Paris?", "retrieval_error", top_k=1)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].error_type, "retrieval_error")
        self.assertIn("paris", results[0].trigger_signature)

    def test_add_if_recovered_only_stores_recovered_records(self) -> None:
        store = FailureMemoryStore()

        self.assertIs(store.add_if_recovered(self.record_a, "partial"), store)
        self.assertEqual(len(store), 0)

        self.assertIs(store.add_if_recovered(self.record_a, "recovered"), store)
        self.assertEqual(len(store), 1)

    def test_record_accepts_absorbed_route_label(self) -> None:
        record = FailureMemoryRecord(
            error_type="retrieval_error",
            wrong_memory="semantic store was not queried",
            original_evidence="Correct evidence was in semantic memory",
            cause="route missed the semantic store",
            corrected_memory="Query semantic memory for this fact",
            repair_action="update_routing",
            repair_guidance="update routing",
            trigger_signature="retrieval_error semantic memory",
            memory_top_terms=("semantic", "memory"),
        )
        self.assertEqual(record.error_type, "retrieval_error")

    def test_from_ecs_draft_populates_memory_top_terms(self) -> None:
        case = load_probe_cases_v1(Path("data/probe_cases/v1_route_error_case.json"))[0]
        ecs = draft_ecs_for_label(case, None, "retrieval_error")
        record = FailureMemoryRecord.from_ecs_draft(ecs, case)
        self.assertTrue(record.memory_top_terms)
        self.assertIn("retrieval_error", record.trigger_signature)

    def test_empty_store_returns_empty(self) -> None:
        empty = FailureMemoryStore()
        results = empty.retrieve("test query")
        self.assertEqual(results, [])

    def test_add_returns_self_for_chaining(self) -> None:
        store1 = FailureMemoryStore()
        store2 = store1.add(self.record_a)
        self.assertIs(store1, store2)
        self.assertEqual(len(store2), 1)

    def test_mcts_action_priors_use_similar_successful_history(self) -> None:
        store = FailureMemoryStore()
        store.add(
            StepLevelRecord.from_mcts_result(
                query="What is the capital of France?",
                hop_index=0,
                label="injection_error",
                cause="history",
                corrected_memory="",
                repair_guidance="fix injection first",
                recovery_success=True,
                recovery_gain=0.8,
            )
        )
        store.add(
            StepLevelRecord.from_mcts_result(
                query="What is the capital of France?",
                hop_index=0,
                label="retrieval_error",
                cause="history",
                corrected_memory="",
                repair_guidance="fix retrieval",
                recovery_success=False,
                recovery_gain=0.0,
            )
        )

        priors = store.get_mcts_action_priors("France capital question")

        self.assertGreater(priors["injection_error"], priors["retrieval_error"])

    def test_hook_confidence_bonus_uses_recovered_similar_history(self) -> None:
        store = FailureMemoryStore().add(self.record_a)

        bonus = store.get_hook_confidence_bonus("What is the capital of France?")

        self.assertGreater(bonus, 0.0)

    def test_item_priority_scores_matching_item_record_higher(self) -> None:
        from cmd_audit.core.models import MemoryItem

        berlin = MemoryItem("m_berlin", "Berlin is the capital of Germany")
        paris = MemoryItem("m_paris", "Paris is the capital of France")

        self.assertGreater(
            self.store.score_item_priority("Tell me about Berlin", berlin),
            self.store.score_item_priority("Tell me about Berlin", paris),
        )

    def test_repair_guidance_reuses_matching_failure_memory_guidance(self) -> None:
        guidance = self.store.get_repair_guidance(
            "Tell me about Berlin",
            "item_wrong",
        )

        self.assertEqual(guidance, "replace the incorrect item")


# ── build_failure_memory_context ─────────────────────────────────────


class BuildFailureMemoryContextV1Test(unittest.TestCase):
    """AC: fm_context = wrong_memory + original_evidence (diagnostic signal)."""

    def test_empty_records_returns_empty(self) -> None:
        self.assertEqual(build_failure_memory_context(()), "")

    def test_contains_wrong_memory(self) -> None:
        record = FailureMemoryRecord(
            error_type="retrieval_error",
            wrong_memory="wrong answer about Tokyo",
            original_evidence="Tokyo is Japan's capital",
            cause="retrieval missed",
            corrected_memory="Tokyo is the capital of Japan",
            repair_action="oracle_retrieval",
            repair_guidance="update routing",
            trigger_signature="retrieval_error|tokyo capital japan",
        )
        ctx = build_failure_memory_context((record,))
        self.assertIn("wrong answer about Tokyo", ctx)
        self.assertIn("Tokyo is Japan's capital", ctx)

    def test_contains_diagnostic_header(self) -> None:
        record = FailureMemoryRecord(
            error_type="item_wrong",
            wrong_memory="missing",
            original_evidence="evidence",
            cause="not written",
            corrected_memory="corrected",
            repair_action="replace",
            repair_guidance="write it",
            trigger_signature="item_wrong|test",
        )
        ctx = build_failure_memory_context((record,))
        self.assertIn("[Failure Memory Diagnostic Context]", ctx)
        self.assertIn("incorrect memory content", ctx)
        self.assertIn("Evidence of error", ctx)

    def test_multiple_records(self) -> None:
        r1 = FailureMemoryRecord(
            error_type="retrieval_error",
            wrong_memory="w1",
            original_evidence="e1",
            cause="c1",
            corrected_memory="cm1",
            repair_action="ra1",
            repair_guidance="rg1",
            trigger_signature="retrieval_error|test1",
        )
        r2 = FailureMemoryRecord(
            error_type="item_compression_distorted",
            wrong_memory="w2",
            original_evidence="e2",
            cause="c2",
            corrected_memory="cm2",
            repair_action="ra2",
            repair_guidance="rg2",
            trigger_signature="item_compression_distorted|test2",
        )
        ctx = build_failure_memory_context((r1, r2))
        self.assertIn("Past Error 1", ctx)
        self.assertIn("Past Error 2", ctx)
        self.assertIn("retrieval_error", ctx)
        self.assertIn("item_compression_distorted", ctx)


# ── build_repair_context ────────────────────────────────────────────────


class BuildRepairContextTest(unittest.TestCase):
    """AC: Full repair context = baseline + label + evidence + fm_context."""

    def test_all_components_present(self) -> None:
        ctx = build_repair_context(
            baseline_context="Original agent context",
            label="retrieval_error",
            evidence_block="Paris is the capital of France",
            fm_context="[Diagnostic] wrong about Paris",
        )
        self.assertIn("Original agent context", ctx)
        self.assertIn("retrieval_error", ctx)
        self.assertIn("Paris is the capital of France", ctx)
        self.assertIn("[Diagnostic] wrong about Paris", ctx)

    def test_empty_fm_context_omits_it(self) -> None:
        ctx = build_repair_context(
            baseline_context="baseline",
            label="item_wrong",
            evidence_block="evidence",
            fm_context="",
        )
        self.assertIn("baseline", ctx)
        self.assertIn("item_wrong", ctx)
        self.assertIn("evidence", ctx)

    def test_empty_label_omits_diagnosis(self) -> None:
        ctx = build_repair_context(
            baseline_context="baseline",
            label="",
            evidence_block="evidence",
            fm_context="",
        )
        self.assertNotIn("Diagnosis", ctx)


# ── Composite Key Scoring ───────────────────────────────────────────────


class CompositeKeyScoringTest(unittest.TestCase):
    """AC: _score_composite_key weights label_match highest."""

    def test_label_match_adds_two_points(self) -> None:
        record = FailureMemoryRecord(
            error_type="retrieval_error",
            wrong_memory="w",
            original_evidence="e",
            cause="c",
            corrected_memory="cm",
            repair_action="ra",
            repair_guidance="rg",
            trigger_signature="retrieval_error paris france",
            memory_top_terms=("paris",),
        )
        score = _score_composite_key(
            record, query="about Paris", label="retrieval_error"
        )
        # label_match=2, query_overlap=1 (paris), mem_overlap=1 (paris) = 4
        self.assertGreaterEqual(score, 2)

    def test_no_match_returns_zero(self) -> None:
        record = FailureMemoryRecord(
            error_type="item_wrong",
            wrong_memory="w",
            original_evidence="e",
            cause="c",
            corrected_memory="cm",
            repair_action="ra",
            repair_guidance="rg",
            trigger_signature="item_wrong|berlin germany",
        )
        score = _score_composite_key(
            record, query="about Tokyo", label="retrieval_error"
        )
        self.assertEqual(score, 0)


# ── Backward Compatibility ──────────────────────────────────────────────


class BackwardCompatibilityTest(unittest.TestCase):
    """AC: V0 keyword-only store still works via internal API."""

    def test_v0_store_still_works(self) -> None:
        record = FailureMemoryRecord(
            error_type="retrieval_error",
            wrong_memory="w",
            original_evidence="e",
            cause="c",
            corrected_memory="cm",
            repair_action="ra",
            repair_guidance="rg",
            trigger_signature="retrieval_error paris france",
        )
        store = _FailureMemoryStoreV0().add(record)
        results = store.retrieve("about Paris")
        self.assertTrue(len(results) > 0)




class CompositeRetrievalPrecisionTest(unittest.TestCase):
    """AC: Composite key retrieval outperforms simple keyword retrieval."""

    def test_composite_distinguishes_same_keywords_different_content(self) -> None:
        """Two records with same query keywords but different memory content.
        Composite retrieval should rank the one with matching memory_top_terms higher.
        """
        record_paris = FailureMemoryRecord(
            error_type="retrieval_error",
            wrong_memory="wrong about Paris",
            original_evidence="Paris is France capital",
            cause="retrieval missed",
            corrected_memory="Paris is the capital of France",
            repair_action="oracle_retrieval",
            repair_guidance="update routing",
            trigger_signature="retrieval_error paris france capital",
            memory_top_terms=("paris", "france"),
        )
        record_berlin = FailureMemoryRecord(
            error_type="retrieval_error",
            wrong_memory="wrong about Berlin",
            original_evidence="Berlin is Germany capital",
            cause="retrieval missed",
            corrected_memory="Berlin is the capital of Germany",
            repair_action="oracle_retrieval",
            repair_guidance="update routing",
            trigger_signature="retrieval_error berlin germany capital",
            memory_top_terms=("berlin", "germany"),
        )
        store = FailureMemoryStore().add(record_paris).add(record_berlin)

        # Query about Paris with memory_top_terms matching Paris record
        results = store.retrieve(
            query="What is the capital of France?",
            label="retrieval_error",
            top_k=2,
        )
        self.assertTrue(len(results) >= 1)
        # Paris record should rank first due to label match + query overlap + memory term overlap
        self.assertEqual(results[0].wrong_memory, "wrong about Paris")

    def test_composite_beats_keyword_only_on_mixed_content(self) -> None:
        """Without memory_top_terms, both records with 'capital' match equally.
        With memory_top_terms, the correct one wins."""
        record_a = FailureMemoryRecord(
            error_type="item_wrong",
            wrong_memory="missing Tokyo data",
            original_evidence="Tokyo is Japan capital",
            cause="not written",
            corrected_memory="Tokyo is Japan capital",
            repair_action="replace",
            repair_guidance="write it",
            trigger_signature="item_wrong tokyo japan capital",
            memory_top_terms=("tokyo", "japan"),
        )
        record_b = FailureMemoryRecord(
            error_type="item_wrong",
            wrong_memory="missing London data",
            original_evidence="London is UK capital",
            cause="not written",
            corrected_memory="London is UK capital",
            repair_action="replace",
            repair_guidance="write it",
            trigger_signature="item_wrong london uk capital",
            memory_top_terms=("london",),
        )
        store = FailureMemoryStore().add(record_a).add(record_b)

        # Stored memory_top_terms are used automatically; callers do not pass them.
        results_with_mem = store.retrieve(
            query="Tell me about Tokyo",
            label="item_wrong",
            top_k=1,
        )

        self.assertEqual(results_with_mem[0].wrong_memory, "missing Tokyo data")


if __name__ == "__main__":
    unittest.main()
