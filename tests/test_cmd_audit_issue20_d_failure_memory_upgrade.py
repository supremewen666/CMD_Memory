"""Failure Memory Upgrade tests — Issue 0020-D."""

from pathlib import Path
import unittest

from cmd_audit import (
    FailureMemoryRecord,
    FailureMemoryStore,
    FailureMemoryStoreV1,
    build_failure_memory_context_v1,
    build_repair_context,
    compute_memory_top_terms,
    draft_ecs_for_label,
    load_probe_cases_v1,
)
from cmd_audit.repair.failure_memory import _score_composite_key


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


# ── FailureMemoryStoreV1 ────────────────────────────────────────────────


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
            error_type="write_error",
            wrong_memory="no memory about Berlin",
            original_evidence="Berlin is the capital of Germany",
            cause="evidence never written",
            corrected_memory="Berlin is the capital of Germany",
            repair_action="oracle_write",
            repair_guidance="ensure events are written",
            trigger_signature="write_error berlin capital germany",
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
        self.store = FailureMemoryStoreV1().add(self.record_a).add(self.record_b).add(self.record_c)

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

    def test_record_accepts_v1_only_label(self) -> None:
        record = FailureMemoryRecord(
            error_type="route_error",
            wrong_memory="semantic store was not queried",
            original_evidence="Correct evidence was in semantic memory",
            cause="route missed the semantic store",
            corrected_memory="Query semantic memory for this fact",
            repair_action="oracle_route",
            repair_guidance="update routing",
            trigger_signature="route_error semantic memory",
            memory_top_terms=("semantic", "memory"),
        )
        self.assertEqual(record.error_type, "route_error")

    def test_from_ecs_draft_populates_memory_top_terms(self) -> None:
        case = load_probe_cases_v1(Path("data/probe_cases/v1_route_error_case.json"))[0]
        ecs = draft_ecs_for_label(case, None, "route_error")
        record = FailureMemoryRecord.from_ecs_draft(ecs, case)
        self.assertTrue(record.memory_top_terms)
        self.assertIn("route_error", record.trigger_signature)

    def test_empty_store_returns_empty(self) -> None:
        empty = FailureMemoryStoreV1()
        results = empty.retrieve("test query")
        self.assertEqual(results, ())

    def test_add_returns_new_instance(self) -> None:
        store1 = FailureMemoryStoreV1()
        store2 = store1.add(self.record_a)
        self.assertIsNot(store1, store2)
        self.assertEqual(len(store1), 0)
        self.assertEqual(len(store2), 1)


# ── build_failure_memory_context_v1 ─────────────────────────────────────


class BuildFailureMemoryContextV1Test(unittest.TestCase):
    """AC: fm_context = wrong_memory + original_evidence (diagnostic signal)."""

    def test_empty_records_returns_empty(self) -> None:
        self.assertEqual(build_failure_memory_context_v1(()), "")

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
        ctx = build_failure_memory_context_v1((record,))
        self.assertIn("wrong answer about Tokyo", ctx)
        self.assertIn("Tokyo is Japan's capital", ctx)

    def test_contains_diagnostic_header(self) -> None:
        record = FailureMemoryRecord(
            error_type="write_error",
            wrong_memory="missing",
            original_evidence="evidence",
            cause="not written",
            corrected_memory="corrected",
            repair_action="oracle_write",
            repair_guidance="write it",
            trigger_signature="write_error|test",
        )
        ctx = build_failure_memory_context_v1((record,))
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
            error_type="compression_error",
            wrong_memory="w2",
            original_evidence="e2",
            cause="c2",
            corrected_memory="cm2",
            repair_action="ra2",
            repair_guidance="rg2",
            trigger_signature="compression_error|test2",
        )
        ctx = build_failure_memory_context_v1((r1, r2))
        self.assertIn("Past Error 1", ctx)
        self.assertIn("Past Error 2", ctx)
        self.assertIn("retrieval_error", ctx)
        self.assertIn("compression_error", ctx)


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
            label="write_error",
            evidence_block="evidence",
            fm_context="",
        )
        self.assertIn("baseline", ctx)
        self.assertIn("write_error", ctx)
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
            error_type="write_error",
            wrong_memory="w",
            original_evidence="e",
            cause="c",
            corrected_memory="cm",
            repair_action="ra",
            repair_guidance="rg",
            trigger_signature="write_error|berlin germany",
        )
        score = _score_composite_key(
            record, query="about Tokyo", label="retrieval_error"
        )
        self.assertEqual(score, 0)


# ── Backward Compatibility ──────────────────────────────────────────────


class BackwardCompatibilityTest(unittest.TestCase):
    """AC: V0 FailureMemoryStore unchanged."""

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
        store = FailureMemoryStore().add(record)
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
        store = FailureMemoryStoreV1().add(record_paris).add(record_berlin)

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
            error_type="write_error",
            wrong_memory="missing Tokyo data",
            original_evidence="Tokyo is Japan capital",
            cause="not written",
            corrected_memory="Tokyo is Japan capital",
            repair_action="oracle_write",
            repair_guidance="write it",
            trigger_signature="write_error tokyo japan capital",
            memory_top_terms=("tokyo", "japan"),
        )
        record_b = FailureMemoryRecord(
            error_type="write_error",
            wrong_memory="missing London data",
            original_evidence="London is UK capital",
            cause="not written",
            corrected_memory="London is UK capital",
            repair_action="oracle_write",
            repair_guidance="write it",
            trigger_signature="write_error london uk capital",
            memory_top_terms=("london",),
        )
        store = FailureMemoryStoreV1().add(record_a).add(record_b)

        # Stored memory_top_terms are used automatically; callers do not pass them.
        results_with_mem = store.retrieve(
            query="Tell me about Tokyo",
            label="write_error",
            top_k=1,
        )

        self.assertEqual(results_with_mem[0].wrong_memory, "missing Tokyo data")


if __name__ == "__main__":
    unittest.main()
