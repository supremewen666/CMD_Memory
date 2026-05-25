"""Behavior-level tests for issue 0017: Provenance Tracking (Execution Lineage DAG + trace-mem Citation).

Covers all 9 acceptance criteria:
  AC16.1 — Data model (Citation, ProvenanceEdge, MemoryItem.provenance)
  AC16.2 — Provenance recording per replay type (10 replays)
  AC16.3 — Provenance completeness metric
  AC16.4 — HMAC tamper detection
  AC16.5 — graph_error distractor edge identification
  AC16.6 — Backward compatibility
  AC16.7 — Adapter compatibility (mem0 + Letta)
  AC16.8 — CSV output columns
  AC16.9 — Paper-facing provenance completeness metrics
"""

from __future__ import annotations

import csv
import hashlib
import time
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from cmd_audit import (
    Citation,
    ProvenanceEdge,
    ProvenanceTracker,
    compute_provenance_completeness,
    detect_tamper,
    get_graph_distractor_edges,
    load_probe_cases,
    load_probe_cases_v1,
    record_provenance_edge,
    run_case,
    run_case_v1,
    run_case_v1_with_hook,
    check_v1_to_v2_gate,
    write_attribution_table,
    write_comparison_metrics_table,
    write_provenance_completeness_summary,
)
from cmd_audit.models import MemoryItem
from cmd_audit.provenance import _compute_hmac
from cmd_audit.replays import (
    ReplayResult,
    run_v0_replay_portfolio,
    run_v1_replay_portfolio,
)

V0_SMOKE = Path("data/probe_cases/v0_issue3_cases.json")
GRAPH_FIXTURE = Path("data/probe_cases/v1_graph_error_case.json")
GRANULARITY_FIXTURE = Path("data/probe_cases/v1_granularity_error_case.json")
SAFETY_FIXTURE = Path("data/probe_cases/v1_safety_error_case.json")
ROUTE_FIXTURE = Path("data/probe_cases/v1_route_error_case.json")
INGESTION_FIXTURE = Path("data/probe_cases/v1_ingestion_error_case.json")
MEM0_TRACES = Path("data/probe_cases/mem0_v0_smoke_traces.json")
LETTA_TRACES = Path("data/probe_cases/letta_v0_smoke_traces.json")


# ── Data Model Tests (AC16.1) ──────────────────────────────────────────────


class DataModelTest(unittest.TestCase):
    """Citation, ProvenanceEdge, and MemoryItem.provenance field validation."""

    def test_citation_is_frozen_dataclass(self) -> None:
        c = Citation(trajectory_turn=0, char_span=(0, 10), content_hash="abc123")
        self.assertEqual(c.trajectory_turn, 0)
        self.assertEqual(c.char_span, (0, 10))
        self.assertEqual(c.content_hash, "abc123")
        with self.assertRaises(Exception):
            c.content_hash = "modified"  # type: ignore[misc]

    def test_citation_fields_are_required(self) -> None:
        with self.assertRaises(TypeError):
            Citation()  # type: ignore[call-arg]

    def test_provenance_edge_is_frozen_dataclass(self) -> None:
        citation = Citation(trajectory_turn=1, char_span=(4, 20), content_hash="hash")
        edge = ProvenanceEdge(
            source_id="src-1", target_id="tgt-1", operation="write",
            citation=citation, timestamp=1000.0,
        )
        self.assertEqual(edge.source_id, "src-1")
        self.assertEqual(edge.target_id, "tgt-1")
        self.assertEqual(edge.operation, "write")
        self.assertEqual(edge.citation, citation)
        self.assertEqual(edge.timestamp, 1000.0)
        self.assertFalse(edge.tamper_detected)

    def test_provenance_edge_tamper_defaults_false(self) -> None:
        citation = Citation(trajectory_turn=0, char_span=(0, 5), content_hash="h")
        edge = ProvenanceEdge(
            source_id="s", target_id="t", operation="compress",
            citation=citation, timestamp=0.0,
        )
        self.assertFalse(edge.tamper_detected)

    def test_memory_item_provenance_defaults_to_empty_tuple(self) -> None:
        item = MemoryItem(memory_id="m1", text="some text")
        self.assertEqual(item.provenance, ())

    def test_memory_item_provenance_with_edges(self) -> None:
        citation = Citation(trajectory_turn=0, char_span=(0, 3), content_hash="h1")
        edge = ProvenanceEdge(
            source_id="s1", target_id="t1", operation="inject",
            citation=citation, timestamp=0.0,
        )
        item = MemoryItem(memory_id="m1", text="text", provenance=(edge,))
        self.assertEqual(len(item.provenance), 1)
        self.assertEqual(item.provenance[0].source_id, "s1")

    def test_from_mapping_parses_provenance(self) -> None:
        raw = {
            "memory_id": "m1", "text": "some memory text",
            "provenance": [{
                "source_id": "s1", "target_id": "t1", "operation": "write",
                "citation": {"trajectory_turn": 0, "char_span": [0, 14], "content_hash": "abcd1234"},
                "timestamp": 1000.0, "tamper_detected": False,
            }],
        }
        item = MemoryItem.from_mapping(raw)
        self.assertEqual(len(item.provenance), 1)
        edge = item.provenance[0]
        self.assertIsInstance(edge, ProvenanceEdge)
        self.assertEqual(edge.source_id, "s1")
        self.assertEqual(edge.target_id, "t1")
        self.assertEqual(edge.operation, "write")
        self.assertIsInstance(edge.citation, Citation)
        self.assertEqual(edge.citation.trajectory_turn, 0)
        self.assertEqual(edge.citation.char_span, (0, 14))
        self.assertEqual(edge.citation.content_hash, "abcd1234")

    def test_from_mapping_without_provenance_defaults_empty(self) -> None:
        raw = {"memory_id": "m1", "text": "text"}
        item = MemoryItem.from_mapping(raw)
        self.assertEqual(item.provenance, ())

    def test_from_mapping_parses_multiple_provenance_edges(self) -> None:
        raw = {
            "memory_id": "m2", "text": "text",
            "provenance": [
                {"source_id": "s1", "target_id": "t1", "operation": "write",
                 "citation": {"trajectory_turn": 0, "char_span": [0, 5], "content_hash": "h1"},
                 "timestamp": 1.0},
                {"source_id": "s2", "target_id": "t1", "operation": "compress",
                 "citation": {"trajectory_turn": 1, "char_span": [0, 5], "content_hash": "h2"},
                 "timestamp": 2.0},
            ],
        }
        item = MemoryItem.from_mapping(raw)
        self.assertEqual(len(item.provenance), 2)
        self.assertEqual(item.provenance[0].source_id, "s1")
        self.assertEqual(item.provenance[1].source_id, "s2")

    def test_memory_item_frozen_precludes_mutation(self) -> None:
        item = MemoryItem(memory_id="m1", text="text")
        with self.assertRaises(Exception):
            item.provenance = ()  # type: ignore[misc]


# ── ProvenanceTracker Tests ────────────────────────────────────────────────


class ProvenanceTrackerTest(unittest.TestCase):
    """ProvenanceTracker collection and annotation behavior."""

    def setUp(self) -> None:
        self.tracker = ProvenanceTracker("test-case-1")

    def test_tracker_stores_case_id(self) -> None:
        self.assertEqual(self.tracker.case_id, "test-case-1")

    def test_session_key_is_sha256_hex(self) -> None:
        key = self.tracker.session_key
        self.assertEqual(len(key), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in key))

    def test_session_key_is_deterministic(self) -> None:
        t1 = ProvenanceTracker("test-case-1")
        t2 = ProvenanceTracker("test-case-1")
        self.assertEqual(t1.session_key, t2.session_key)

    def test_session_key_differs_per_case(self) -> None:
        t1 = ProvenanceTracker("case-a")
        t2 = ProvenanceTracker("case-b")
        self.assertNotEqual(t1.session_key, t2.session_key)

    def test_record_edge_returns_provenance_edge(self) -> None:
        edge = self.tracker.record_edge(
            source_id="src-1", target_id="tgt-1", operation="write",
            source_text="hello world",
        )
        self.assertIsInstance(edge, ProvenanceEdge)
        self.assertEqual(edge.source_id, "src-1")
        self.assertEqual(edge.operation, "write")
        self.assertIsInstance(edge.citation, Citation)
        self.assertFalse(edge.tamper_detected)

    def test_record_edge_hmac_is_computed(self) -> None:
        edge = self.tracker.record_edge(
            source_id="src", target_id="tgt", operation="retrieve",
            source_text="test content",
        )
        expected = _compute_hmac("test content", self.tracker.session_key)
        self.assertEqual(edge.citation.content_hash, expected)

    def test_get_edges_initially_empty(self) -> None:
        self.assertEqual(self.tracker.get_edges(), ())

    def test_get_edges_returns_all_recorded(self) -> None:
        self.tracker.record_edge("s1", "t1", "write", "a")
        self.tracker.record_edge("s2", "t2", "compress", "b")
        edges = self.tracker.get_edges()
        self.assertEqual(len(edges), 2)

    def test_annotate_item_no_edges_returns_same_item(self) -> None:
        item = MemoryItem(memory_id="m1", text="hello")
        result = self.tracker.annotate_item(item)
        self.assertIs(result, item)

    def test_annotate_item_bakes_provenance(self) -> None:
        item = MemoryItem(memory_id="m1", text="hello")
        self.tracker.record_edge("s1", "m1", "write", "hello")
        annotated = self.tracker.annotate_item(item)
        self.assertEqual(len(annotated.provenance), 1)
        self.assertEqual(annotated.provenance[0].source_id, "s1")
        self.assertEqual(annotated.text, "hello")
        self.assertEqual(annotated.memory_id, "m1")

    def test_annotate_item_explicit_target_id(self) -> None:
        item = MemoryItem(memory_id="m1", text="hello")
        self.tracker.record_edge("s1", "custom-tgt", "write", "hello")
        annotated = self.tracker.annotate_item(item, target_id="custom-tgt")
        self.assertEqual(len(annotated.provenance), 1)

    def test_annotate_item_accumulates_multiple_edges(self) -> None:
        item = MemoryItem(memory_id="m1", text="hello")
        self.tracker.record_edge("s1", "m1", "write", "a")
        self.tracker.record_edge("s2", "m1", "compress", "b")
        annotated = self.tracker.annotate_item(item)
        self.assertEqual(len(annotated.provenance), 2)


# ── Convenience Wrapper Test ───────────────────────────────────────────────


class ConvenienceWrapperTest(unittest.TestCase):
    """record_provenance_edge module-level convenience wrapper."""

    def test_wrapper_delegates_to_tracker(self) -> None:
        tracker = ProvenanceTracker("case-x")
        edge = record_provenance_edge(
            tracker, source_id="s1", target_id="t1", operation="extract",
            source_text="test", char_span=(0, 4), trajectory_turn=2,
        )
        self.assertEqual(edge.citation.trajectory_turn, 2)
        self.assertEqual(edge.citation.char_span, (0, 4))
        self.assertEqual(len(tracker.get_edges()), 1)


# ── Provenance Recording per Replay (AC16.2) ───────────────────────────────


class ProvenanceRecordingTest(unittest.TestCase):
    """Each of the 10 V1 replay types records provenance when tracker is provided."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.v0_cases = load_probe_cases(V0_SMOKE)
        cls._v0_map = {c.case_id: c for c in cls.v0_cases}
        cls.v1_graph_case = load_probe_cases_v1(GRAPH_FIXTURE)[0]
        cls.v1_granularity_case = load_probe_cases_v1(GRANULARITY_FIXTURE)[0]
        cls.v1_safety_case = load_probe_cases_v1(SAFETY_FIXTURE)[0]
        cls.v1_route_case = load_probe_cases_v1(ROUTE_FIXTURE)[0]

    def test_oracle_write_records_provenance(self) -> None:
        case = self._v0_map["v0-write-001"]
        tracker = ProvenanceTracker(case.case_id)
        from cmd_audit.replays import run_oracle_write
        run_oracle_write(case, tracker=tracker)
        edges = tracker.get_edges()
        self.assertGreater(len(edges), 0)
        self.assertEqual(edges[0].operation, "write")

    def test_oracle_compression_records_provenance(self) -> None:
        case = self._v0_map["v0-compression-001"]
        tracker = ProvenanceTracker(case.case_id)
        from cmd_audit.replays import run_oracle_compression
        run_oracle_compression(case, tracker=tracker)
        edges = tracker.get_edges()
        self.assertGreater(len(edges), 0)
        self.assertEqual(edges[0].operation, "compress")

    def test_verbatim_event_oracle_records_provenance(self) -> None:
        case = self._v0_map["v0-premature-extraction-001"]
        tracker = ProvenanceTracker(case.case_id)
        from cmd_audit.replays import run_verbatim_event_oracle
        run_verbatim_event_oracle(case, tracker=tracker)
        edges = tracker.get_edges()
        self.assertGreater(len(edges), 0)
        self.assertEqual(edges[0].operation, "extract")

    def test_oracle_retrieval_records_provenance(self) -> None:
        case = self._v0_map["v0-retrieval-001"]
        tracker = ProvenanceTracker(case.case_id)
        from cmd_audit.replays import run_oracle_retrieval
        run_oracle_retrieval(case, tracker=tracker)
        edges = tracker.get_edges()
        self.assertGreater(len(edges), 0)
        self.assertEqual(edges[0].operation, "retrieve")

    def test_injection_oracle_records_provenance(self) -> None:
        case = self._v0_map["v0-injection-001"]
        tracker = ProvenanceTracker(case.case_id)
        from cmd_audit.replays import run_injection_oracle
        result = run_injection_oracle(case, tracker=tracker)
        if result.evidence_score < 1.0:
            edges = tracker.get_edges()
            self.assertGreater(len(edges), 0)
            self.assertEqual(edges[0].operation, "inject")

    def test_evidence_given_reasoning_records_provenance(self) -> None:
        case = self._v0_map["v0-reasoning-001"]
        tracker = ProvenanceTracker(case.case_id)
        from cmd_audit.replays import run_evidence_given_reasoning
        result = run_evidence_given_reasoning(case, tracker=tracker)
        if result.evidence_block:
            edges = tracker.get_edges()
            self.assertGreater(len(edges), 0)

    def test_oracle_route_records_provenance(self) -> None:
        case = self.v1_route_case
        tracker = ProvenanceTracker(case.case_id)
        from cmd_audit.replays import run_oracle_route
        result = run_oracle_route(case, tracker=tracker)
        if result.evidence_block:
            edges = tracker.get_edges()
            self.assertGreater(len(edges), 0)
            self.assertEqual(edges[0].operation, "route")

    def test_oracle_granularity_records_provenance(self) -> None:
        case = self.v1_granularity_case
        tracker = ProvenanceTracker(case.case_id)
        from cmd_audit.replays import run_oracle_granularity
        result = run_oracle_granularity(case, tracker=tracker)
        if result.evidence_block:
            edges = tracker.get_edges()
            self.assertGreater(len(edges), 0)
            self.assertEqual(edges[0].operation, "extract")

    def test_graph_off_records_provenance(self) -> None:
        case = self.v1_graph_case
        tracker = ProvenanceTracker(case.case_id)
        from cmd_audit.replays import run_graph_off
        run_graph_off(case, tracker=tracker)
        edges = tracker.get_edges()
        self.assertGreater(len(edges), 0)
        self.assertEqual(edges[0].operation, "retrieve")

    def test_safety_off_records_provenance(self) -> None:
        case = self.v1_safety_case
        tracker = ProvenanceTracker(case.case_id)
        from cmd_audit.replays import run_safety_off
        result = run_safety_off(case, tracker=tracker)
        if result.evidence_block:
            edges = tracker.get_edges()
            self.assertGreater(len(edges), 0)
            self.assertEqual(edges[0].operation, "inject")

    def test_replay_result_carries_provenance_edges(self) -> None:
        case = self._v0_map["v0-write-001"]
        tracker = ProvenanceTracker(case.case_id)
        from cmd_audit.replays import run_oracle_write
        result = run_oracle_write(case, tracker=tracker)
        self.assertIsInstance(result.provenance_edges, tuple)
        self.assertGreater(len(result.provenance_edges), 0)
        for edge in result.provenance_edges:
            self.assertIsInstance(edge, ProvenanceEdge)

    def test_v0_portfolio_forwards_tracker(self) -> None:
        case = self._v0_map["v0-write-001"]
        tracker = ProvenanceTracker(case.case_id)
        replays = run_v0_replay_portfolio(case, tracker=tracker)
        total_edges = sum(len(r.provenance_edges) for r in replays)
        self.assertGreater(total_edges, 0)

    def test_v1_portfolio_forwards_tracker(self) -> None:
        case = self._v0_map["v0-write-001"]
        tracker = ProvenanceTracker(case.case_id)
        replays = run_v1_replay_portfolio(case, tracker=tracker)
        total_edges = sum(len(r.provenance_edges) for r in replays)
        self.assertGreater(total_edges, 0)


# ── Provenance Completeness (AC16.3) ───────────────────────────────────────


class ProvenanceCompletenessTest(unittest.TestCase):
    """compute_provenance_completeness metric."""

    def _make_edge(self) -> ProvenanceEdge:
        citation = Citation(trajectory_turn=0, char_span=(0, 2), content_hash="h")
        return ProvenanceEdge(source_id="s", target_id="t", operation="write",
                              citation=citation, timestamp=0.0)

    def test_all_items_have_provenance(self) -> None:
        edge = self._make_edge()
        items = (
            MemoryItem(memory_id="m1", text="a", provenance=(edge,)),
            MemoryItem(memory_id="m2", text="b", provenance=(edge,)),
        )
        self.assertAlmostEqual(compute_provenance_completeness(items), 1.0)

    def test_partial_items_have_provenance(self) -> None:
        edge = self._make_edge()
        items = (
            MemoryItem(memory_id="m1", text="a", provenance=(edge,)),
            MemoryItem(memory_id="m2", text="b"),
        )
        self.assertAlmostEqual(compute_provenance_completeness(items), 0.5)

    def test_no_items_have_provenance(self) -> None:
        items = (
            MemoryItem(memory_id="m1", text="a"),
            MemoryItem(memory_id="m2", text="b"),
        )
        self.assertAlmostEqual(compute_provenance_completeness(items), 0.0)

    def test_empty_items_returns_zero(self) -> None:
        self.assertAlmostEqual(compute_provenance_completeness(()), 0.0)

    def test_single_item_with_provenance(self) -> None:
        edge = self._make_edge()
        items = (MemoryItem(memory_id="m1", text="a", provenance=(edge,)),)
        self.assertAlmostEqual(compute_provenance_completeness(items), 1.0)


# ── Tamper Detection (AC16.4) ──────────────────────────────────────────────


class TamperDetectionTest(unittest.TestCase):
    """HMAC-based tamper detection."""

    def setUp(self) -> None:
        self.session_key = hashlib.sha256(b"tamper-test").hexdigest()
        self.content = "original content"
        self.content_hash = _compute_hmac(self.content, self.session_key)
        self.citation = Citation(
            trajectory_turn=0, char_span=(0, len(self.content)),
            content_hash=self.content_hash,
        )
        self.edge = ProvenanceEdge(
            source_id="s1", target_id="t1", operation="write",
            citation=self.citation, timestamp=time.time(),
        )

    def test_valid_content_passes(self) -> None:
        self.assertFalse(detect_tamper(self.edge, self.content, self.session_key))

    def test_modified_content_detected(self) -> None:
        self.assertTrue(detect_tamper(self.edge, "modified content", self.session_key))

    def test_wrong_session_key_detected(self) -> None:
        wrong_key = hashlib.sha256(b"different-case").hexdigest()
        self.assertTrue(detect_tamper(self.edge, self.content, wrong_key))

    def test_empty_content_detected(self) -> None:
        self.assertTrue(detect_tamper(self.edge, "", self.session_key))

    def test_tamper_detection_is_deterministic(self) -> None:
        self.assertFalse(detect_tamper(self.edge, self.content, self.session_key))
        self.assertFalse(detect_tamper(self.edge, self.content, self.session_key))


# ── Graph Error Distractor Edges (AC16.5) ──────────────────────────────────


class GraphErrorProvenanceTest(unittest.TestCase):
    """get_graph_distractor_edges identifies graph-expanded distractors."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.graph_case = load_probe_cases_v1(GRAPH_FIXTURE)[0]

    def test_no_edges_when_no_graph_expanded_items(self) -> None:
        cases = load_probe_cases(V0_SMOKE)
        case = cases[0]
        from cmd_audit.replays import run_graph_off
        result = run_graph_off(case, tracker=None)
        edges = get_graph_distractor_edges(case, result)
        self.assertEqual(edges, ())

    def test_no_edges_when_no_recovery_gain(self) -> None:
        result = ReplayResult(
            replay_name="graph_off", answer="", answer_score=0.0,
            evidence_score=0.0, evidence_block="", recovery_gain=0.0,
        )
        edges = get_graph_distractor_edges(self.graph_case, result)
        self.assertEqual(edges, ())

    def test_distractor_edges_are_provenance_edges(self) -> None:
        from cmd_audit.replays import run_graph_off
        tracker = ProvenanceTracker(self.graph_case.case_id)
        result = run_graph_off(self.graph_case, tracker=tracker)
        edges = get_graph_distractor_edges(self.graph_case, result)
        for edge in edges:
            self.assertIsInstance(edge, ProvenanceEdge)
            self.assertEqual(edge.operation, "retrieve")

    def test_distractor_ids_flow_into_attribution(self) -> None:
        from cmd_audit.attribution import assign_attribution_v1
        case = self.graph_case
        tracker = ProvenanceTracker(case.case_id)
        from cmd_audit.replays import run_graph_off
        graph_off = run_graph_off(case, tracker=tracker)
        distractor_edges = get_graph_distractor_edges(case, graph_off)
        replays = run_v1_replay_portfolio(case, tracker=tracker)
        attribution = assign_attribution_v1(
            replays,
            has_ingestion_trace=case.has_ingestion_trace,
            top_k=2,
            distractor_edges=distractor_edges,
        )
        if distractor_edges:
            self.assertGreater(len(attribution.distractor_provenance_ids), 0)
            self.assertEqual(
                len(attribution.distractor_provenance_ids),
                len(attribution.distractor_provenance_edges),
            )

    def test_distractor_edges_produced_for_graph_case(self) -> None:
        from cmd_audit.replays import run_graph_off
        tracker = ProvenanceTracker(self.graph_case.case_id)
        result = run_graph_off(self.graph_case, tracker=tracker)
        edges = get_graph_distractor_edges(self.graph_case, result)
        # graph_error case has graph-expanded items with positive recovery
        self.assertGreater(len(edges), 0)


# ── Backward Compatibility (AC16.6) ────────────────────────────────────────


class BackwardCompatibilityTest(unittest.TestCase):
    """Backward compatibility: old data, no tracker, default fields."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(V0_SMOKE)

    def test_old_memory_item_without_provenance_works(self) -> None:
        raw = {"memory_id": "m1", "text": "some text", "source_event_ids": ["e1"]}
        item = MemoryItem.from_mapping(raw)
        self.assertEqual(item.provenance, ())
        self.assertEqual(item.memory_id, "m1")

    def test_replay_without_tracker_returns_empty_provenance(self) -> None:
        case = self.cases[0]
        from cmd_audit.replays import run_oracle_write
        result = run_oracle_write(case, tracker=None)
        self.assertEqual(result.provenance_edges, ())

    def test_v0_portfolio_without_tracker_empty_provenance(self) -> None:
        case = self.cases[0]
        replays = run_v0_replay_portfolio(case, tracker=None)
        for replay in replays:
            self.assertEqual(replay.provenance_edges, ())

    def test_v1_portfolio_without_tracker_empty_provenance(self) -> None:
        case = self.cases[0]
        replays = run_v1_replay_portfolio(case, tracker=None)
        for replay in replays:
            self.assertEqual(replay.provenance_edges, ())

    def test_attribution_result_defaults(self) -> None:
        from cmd_audit.attribution import assign_attribution
        case = self.cases[0]
        from cmd_audit.replays import run_oracle_write
        result = run_oracle_write(case)
        attr = assign_attribution((result,))
        self.assertEqual(attr.distractor_provenance_ids, ())
        self.assertEqual(attr.distractor_provenance_edges, ())

    def test_attribution_v1_defaults(self) -> None:
        from cmd_audit.attribution import assign_attribution_v1
        case = self.cases[0]
        from cmd_audit.replays import run_oracle_write
        result = run_oracle_write(case)
        attr = assign_attribution_v1((result,))
        self.assertEqual(attr.distractor_provenance_ids, ())
        self.assertEqual(attr.distractor_provenance_edges, ())

    def test_run_case_v0_still_works(self) -> None:
        for case in self.cases:
            with self.subTest(case_id=case.case_id):
                result = run_case(case)
                self.assertIsNotNone(result.attribution)

    def test_run_case_v1_produces_provenance(self) -> None:
        case = self.cases[0]
        result = run_case_v1(case)
        total_prov = sum(len(r.provenance_edges) for r in result.replays)
        self.assertGreater(total_prov, 0)

    def test_replay_result_default_constructor_no_provenance(self) -> None:
        result = ReplayResult(
            replay_name="test", answer="", answer_score=0.0,
            evidence_score=0.0, evidence_block="", recovery_gain=0.0,
        )
        self.assertEqual(result.provenance_edges, ())


# ── Adapter Provenance (AC16.7) ────────────────────────────────────────────


class AdapterProvenanceTest(unittest.TestCase):
    """Mem0 and Letta adapter paths record provenance edges."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.v0_cases = load_probe_cases(V0_SMOKE)
        cls._v0_map = {c.case_id: c for c in cls.v0_cases}
        from cmd_audit.adapters import load_mem0_traces, load_letta_traces
        cls.mem0_traces = load_mem0_traces(MEM0_TRACES)
        cls.letta_traces = load_letta_traces(LETTA_TRACES)

    def test_mem0_adapter_replay_portfolio_records_provenance(self) -> None:
        from cmd_audit.adapters import Mem0Adapter, run_mem0_replay_portfolio
        case = self._v0_map["v0-write-001"]
        trace = self.mem0_traces[case.case_id]
        adapter = Mem0Adapter(trace, case.gold_evidence, case.extracted_memory, case.raw_events)
        tracker = ProvenanceTracker(case.case_id)
        replays = run_mem0_replay_portfolio(case, adapter, tracker=tracker)
        total_edges = sum(len(r.provenance_edges) for r in replays)
        self.assertGreater(total_edges, 0)

    def test_letta_adapter_replay_portfolio_records_provenance(self) -> None:
        from cmd_audit.adapters import LettaAdapter, run_letta_replay_portfolio
        case = self._v0_map["v0-write-001"]
        trace = self.letta_traces[case.case_id]
        adapter = LettaAdapter(trace, case.gold_evidence, case.extracted_memory, case.raw_events)
        tracker = ProvenanceTracker(case.case_id)
        replays = run_letta_replay_portfolio(case, adapter, tracker=tracker)
        total_edges = sum(len(r.provenance_edges) for r in replays)
        self.assertGreater(total_edges, 0)

    def test_mem0_adapter_case_runner_integrates_provenance(self) -> None:
        from cmd_audit.adapters import run_case_with_mem0
        case = self._v0_map["v0-write-001"]
        trace = self.mem0_traces[case.case_id]
        result = run_case_with_mem0(case, trace)
        total_prov = sum(len(r.provenance_edges) for r in result.replays)
        self.assertGreater(total_prov, 0)

    def test_letta_adapter_case_runner_integrates_provenance(self) -> None:
        from cmd_audit.adapters import run_case_with_letta
        case = self._v0_map["v0-write-001"]
        trace = self.letta_traces[case.case_id]
        result = run_case_with_letta(case, trace)
        total_prov = sum(len(r.provenance_edges) for r in result.replays)
        self.assertGreater(total_prov, 0)

    def test_mem0_provenance_edges_are_valid(self) -> None:
        from cmd_audit.adapters import Mem0Adapter, run_mem0_replay_portfolio
        case = self._v0_map["v0-compression-001"]
        trace = self.mem0_traces[case.case_id]
        adapter = Mem0Adapter(trace, case.gold_evidence, case.extracted_memory, case.raw_events)
        tracker = ProvenanceTracker(case.case_id)
        replays = run_mem0_replay_portfolio(case, adapter, tracker=tracker)
        valid_ops = {"write", "compress", "extract", "retrieve", "inject", "route", "reason"}
        for replay in replays:
            for edge in replay.provenance_edges:
                with self.subTest(replay=replay.replay_name):
                    self.assertIsInstance(edge, ProvenanceEdge)
                    self.assertIsInstance(edge.citation, Citation)
                    self.assertTrue(edge.source_id)
                    self.assertIn(edge.operation, valid_ops)

    def test_letta_provenance_edges_are_valid(self) -> None:
        from cmd_audit.adapters import LettaAdapter, run_letta_replay_portfolio
        case = self._v0_map["v0-compression-001"]
        trace = self.letta_traces[case.case_id]
        adapter = LettaAdapter(trace, case.gold_evidence, case.extracted_memory, case.raw_events)
        tracker = ProvenanceTracker(case.case_id)
        replays = run_letta_replay_portfolio(case, adapter, tracker=tracker)
        valid_ops = {"write", "compress", "extract", "retrieve", "inject", "route", "reason"}
        for replay in replays:
            for edge in replay.provenance_edges:
                with self.subTest(replay=replay.replay_name):
                    self.assertIsInstance(edge, ProvenanceEdge)
                    self.assertIsInstance(edge.citation, Citation)
                    self.assertTrue(edge.source_id)
                    self.assertIn(edge.operation, valid_ops)

    def test_v1_adapter_passthrough_uses_adapter_source_namespace(self) -> None:
        from cmd_audit.adapters import Mem0Adapter, Mem0Trace, run_mem0_replay_portfolio

        case = load_probe_cases_v1(ROUTE_FIXTURE)[0]
        baseline_ids = set(case.primary_baseline.retrieved_memory_ids)
        search_results = tuple(
            item for item in case.extracted_memory if item.memory_id in baseline_ids
        )
        add_inputs = tuple(item.text for item in case.extracted_memory)
        checksum = hashlib.sha256("|".join(sorted(add_inputs)).encode()).hexdigest()
        trace = Mem0Trace(
            case_id=case.case_id,
            add_inputs=add_inputs,
            search_query=case.query,
            search_results=search_results,
            store_checksum=checksum,
        )
        adapter = Mem0Adapter(trace, case.gold_evidence, case.extracted_memory, case.raw_events)
        tracker = ProvenanceTracker(case.case_id)
        replays = run_mem0_replay_portfolio(case, adapter, tracker=tracker)
        route = next(r for r in replays if r.replay_name == "oracle_route")
        self.assertGreater(len(route.provenance_edges), 0)
        self.assertTrue(
            all(edge.source_id.startswith("adapter_input_") for edge in route.provenance_edges)
        )


# ── CSV Output (AC16.8) ────────────────────────────────────────────────────


class CSVOutputTest(unittest.TestCase):
    """Attribution and comparison-metrics CSV include provenance columns."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(V0_SMOKE)

    def test_attribution_table_has_distractor_provenance_ids_column(self) -> None:
        results = [run_case_v1(case) for case in self.cases]
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "attribution.csv"
            write_attribution_table(results, path)
            content = path.read_text()
            self.assertIn("distractor_provenance_ids", content)
            reader = csv.DictReader(path.open())
            for row in reader:
                self.assertIn("distractor_provenance_ids", row)

    def test_comparison_metrics_table_has_provenance_completeness(self) -> None:
        results = [run_case_v1(case) for case in self.cases]
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "comparison.csv"
            write_comparison_metrics_table(results, path)
            content = path.read_text()
            self.assertIn("provenance_completeness", content)
            reader = csv.DictReader(path.open())
            self.assertIn("provenance_completeness", reader.fieldnames or [])
            cmd_rows = [r for r in reader if r.get("system_name") == "CMD-Audit"]
            found = any(r.get("provenance_completeness") for r in cmd_rows)
            self.assertTrue(found, "No provenance_completeness row for CMD-Audit")

    def test_provenance_completeness_summary_writer(self) -> None:
        result = run_case_v1(load_probe_cases_v1(GRAPH_FIXTURE)[0])
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "provenance_completeness.csv"
            write_provenance_completeness_summary([result], path)
            rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
        self.assertEqual(rows[0]["case_id"], result.case_id)
        self.assertIn("provenance_completeness", rows[0])


class ProvenanceGateTamperTest(unittest.TestCase):
    """V1->V2 gate rejects tampered distractor provenance edges."""

    def test_tampered_distractor_edge_fails_gate(self) -> None:
        tracker = ProvenanceTracker("case-tamper")
        edge = tracker.record_edge(
            source_id="adapter_input_0",
            target_id="case-tamper__answer",
            operation="retrieve",
            source_text="original source",
        )
        tampered = replace(edge, source_text="changed source")
        audit_result = SimpleNamespace(
            case_id="case-tamper",
            attribution=SimpleNamespace(distractor_provenance_edges=(tampered,)),
        )
        result = check_v1_to_v2_gate(
            mem0_integrated=True,
            letta_integrated=True,
            audit_results=(audit_result,),
        )
        criterion = [
            c for c in result.criteria if c.criterion_id == "provenance_hmac_tamper_free"
        ][0]
        self.assertFalse(criterion.passed)
        self.assertFalse(result.all_passed)


# ── Harness Integration (AC16.9) ───────────────────────────────────────────


class HarnessIntegrationTest(unittest.TestCase):
    """Provenance tracking fully integrated in V1 harness pipeline."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.v0_cases = load_probe_cases(V0_SMOKE)
        cls.graph_case = load_probe_cases_v1(GRAPH_FIXTURE)[0]

    def test_run_case_v1_all_replays_have_provenance_field(self) -> None:
        case = self.v0_cases[0]
        result = run_case_v1(case)
        for replay in result.replays:
            with self.subTest(replay=replay.replay_name):
                self.assertIsInstance(replay.provenance_edges, tuple)

    def test_run_case_v1_graph_error_gets_distractors(self) -> None:
        case = self.graph_case
        result = run_case_v1(case)
        self.assertGreater(len(result.attribution.distractor_provenance_ids), 0)

    def test_run_case_v1_graph_off_replay_produces_recovery(self) -> None:
        case = self.graph_case
        result = run_case_v1(case)
        graph_off = result.replay_by_name("graph_off")
        self.assertGreater(graph_off.recovery_gain, 0)

    def test_run_case_v1_with_hook_includes_provenance(self) -> None:
        case = self.graph_case
        result = run_case_v1_with_hook(case)
        for replay in result.replays:
            self.assertIsInstance(replay.provenance_edges, tuple)

    def test_provenance_edges_have_hmac_content_hashes(self) -> None:
        case = self.v0_cases[0]
        result = run_case_v1(case)
        for replay in result.replays:
            for edge in replay.provenance_edges:
                with self.subTest(replay=replay.replay_name):
                    self.assertEqual(len(edge.citation.content_hash), 64)
                    self.assertTrue(
                        all(c in "0123456789abcdef" for c in edge.citation.content_hash))

    def test_all_ten_replay_names_available(self) -> None:
        case = self.graph_case
        tracker = ProvenanceTracker(case.case_id)
        replays = run_v1_replay_portfolio(case, tracker=tracker)
        self.assertEqual(len(replays), 10)
        replays_with_edges = sum(1 for r in replays if r.provenance_edges)
        self.assertGreaterEqual(replays_with_edges, 3)


# ── HMAC Computation Test ──────────────────────────────────────────────────


class HMACComputationTest(unittest.TestCase):
    """Internal _compute_hmac behavior."""

    def test_hmac_is_deterministic(self) -> None:
        h1 = _compute_hmac("hello", "key")
        h2 = _compute_hmac("hello", "key")
        self.assertEqual(h1, h2)

    def test_hmac_differs_by_content(self) -> None:
        h1 = _compute_hmac("hello", "key")
        h2 = _compute_hmac("world", "key")
        self.assertNotEqual(h1, h2)

    def test_hmac_differs_by_key(self) -> None:
        h1 = _compute_hmac("hello", "key1")
        h2 = _compute_hmac("hello", "key2")
        self.assertNotEqual(h1, h2)

    def test_hmac_is_64_char_hex(self) -> None:
        h = _compute_hmac("content", "session-key")
        self.assertEqual(len(h), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in h))
